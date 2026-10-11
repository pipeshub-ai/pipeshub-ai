"""MCP client on the mcp SDK: builds the right transport for an MCP instance and exposes a thin
async interface for listing and calling its tools.

Two things about the SDK shape this module:

- Its HTTP errors carry no status, and its JSON-RPC error codes don't say what happened (servers
  send their own). Each operation is recorded on the wire (`app.agents.mcp.wire`) and its
  failures are read from that record.
- A session's task group has to be entered and left in one task, and a transport failure in it
  cancels that task. So every session runs in a task of its own (`_SessionRunner`), and the URL
  guard answers a request the transport couldn't carry with a stand-in response instead of an
  exception that would end the session.
"""
import asyncio
import importlib.metadata
import logging
import math
import os
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Optional

import anyio
import httpx
import httpx2
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.agents.mcp import wire
from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPConnectionLostError,
    MCPHttpStatusError,
    MCPLaunchRefusedError,
    MCPRequestLostError,
    MCPRequestNotSentError,
    MCPSessionExpiredError,
    MCPUrlBlockedError,
    first_leaf,
    is_http_unauthorized,
)
from app.agents.mcp.models import MCPServerConfig, MCPTransport
from app.agents.mcp.stdio_policy import (
    StdioPolicyError,
    rejected_env_names,
    resolve_stdio_launch,
)
from app.agents.mcp.url_guard import (
    check_mcp_url,
    guarded_mcp_http_client,
    guarded_mcp_http_client_factory,
    private_network_allowed,
)
from app.utils.env_utils import env_int
from app.utils.url_redaction import redact_url, redact_urls_in_text
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT
from mcp.shared.dispatcher import ProgressFnT
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CONNECTION_CLOSED,
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
    Implementation,
    ToolListChangedNotification,
)

logger = logging.getLogger(__name__)


def _client_info() -> Implementation:
    """How PipesHub introduces itself to a server (`clientInfo`), instead of the SDK's default."""
    try:
        version = importlib.metadata.version("ai-service")
    except importlib.metadata.PackageNotFoundError:
        version = "0.1"
    return Implementation(name="PipesHub", version=version)


CLIENT_INFO = _client_info()
# Servers already warned about a tool list cut at `_MAX_TOOL_PAGES`.
_truncation_warned: set[str] = set()

# Bounds opening a session: the connection, the handshake and the SDK's version probe.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0
# Bounds one tool call. A tool doing real work can take far longer than a connection.
DEFAULT_CALL_TIMEOUT_SECONDS = 60.0
_READ_TIMEOUT_MARGIN_SECONDS = 30.0
# The SDK's own clock on a call is set this far past ours, which it only backs up: it doesn't
# run while a request is being written, and progress doesn't restart it.
_OUTER_TIMEOUT_MARGIN_SECONDS = 15.0
# A call the server keeps alive with progress still ends here.
MAX_CALL_SECONDS_ENV = "MCP_TOOL_CALL_MAX_SECONDS"
DEFAULT_MAX_CALL_SECONDS = 30 * 60
# Closing a session: a local server gets the SDK's bounded shutdown (about 7 s at worst), an
# HTTP one a DELETE.
_CLOSE_TIMEOUT_SECONDS = 10.0
# Listing tools on an open session. Discovery's budget is this plus the connect timeout.
LIST_TOOLS_TIMEOUT_SECONDS = 15.0
_MAX_TOOL_PAGES = 100
# How long a server found to speak only the initialize handshake skips the version probe.
_REMEMBERED_HANDSHAKE_SECONDS = 3600.0

# The SDK starts a local server with only PATH, HOME and a few like them. These are what an
# operator sets for the host to reach the network (proxies, extra CA certificates, package
# mirrors); without them npx and uvx can't download or connect behind a corporate proxy.
INHERITED_NETWORK_ENV = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
    "NPM_CONFIG_REGISTRY", "npm_config_registry", "NPM_CONFIG_CAFILE", "npm_config_cafile",
    "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "UV_NATIVE_TLS",
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_CERT",
)


def inherited_network_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for name in INHERITED_NETWORK_ENV:
        # Windows names are case-insensitive: there `http_proxy` is `HTTP_PROXY` again.
        if os.name == "nt" and name != name.upper():
            continue
        if value := os.environ.get(name):
            env[name] = value
    return env


def connect_timeout(config: MCPServerConfig) -> float:
    return config.connect_timeout_seconds or DEFAULT_CONNECT_TIMEOUT_SECONDS


def call_timeout(config: MCPServerConfig) -> float:
    return config.call_timeout_seconds or DEFAULT_CALL_TIMEOUT_SECONDS


def max_call_seconds(config: MCPServerConfig) -> float:
    """How long a call may run while the server keeps reporting progress, each report restarting
    the call timeout: `MCP_TOOL_CALL_MAX_SECONDS`, 30 minutes by default, never below the call
    timeout."""
    return max(float(env_int(MAX_CALL_SECONDS_ENV, DEFAULT_MAX_CALL_SECONDS, lo=1) or 0), call_timeout(config))


def http_read_timeout(config: MCPServerConfig) -> float:
    """How long an HTTP read may wait for the server. It has to outlast the call timeout:
    a shorter read timeout ends a long call first."""
    return max(MCP_DEFAULT_SSE_READ_TIMEOUT, call_timeout(config) + _READ_TIMEOUT_MARGIN_SECONDS)


def discovery_timeout(config: MCPServerConfig) -> float:
    """One budget for discovering a server's tools wherever it happens — chat, the builder
    and the workspace pages — so a server can't list tools in one and time out in another."""
    return connect_timeout(config) + LIST_TOOLS_TIMEOUT_SECONDS


# The SDK sends a local server's stderr to the parent's own, and reports a failed start as a
# bare "Connection closed". Captured to a temp file, the real reason (`npx: command not found`,
# a registry timeout, the server's own "missing API key") goes into the error.
_STDERR_TAIL_MAX_CHARS = 4000


@dataclass
class _Transport:
    """The SDK transport for one session, and what this module has to reach or close itself:
    the HTTP clients whose headers carry the credentials, and the stderr capture file."""

    streams: Any
    http_clients: list["httpx2.AsyncClient"] = field(default_factory=list)
    # The SDK closes an SSE client it created, but not a streamable-HTTP client it was given.
    owned_http_client: Optional["httpx2.AsyncClient"] = None
    stderr: Optional[IO[str]] = None

    def set_headers(self, headers: dict[str, str]) -> None:
        for client in self.http_clients:
            client.headers.update(headers)

    async def aclose(self) -> None:
        if self.owned_http_client is not None:
            with suppress(Exception):
                await self.owned_http_client.aclose()
        if self.stderr is not None:
            with suppress(OSError):
                self.stderr.close()


def build_transport(
    config: MCPServerConfig,
    env: Optional[dict[str, str]] = None,
    headers: Optional[dict[str, str]] = None,
    stderr_log_file: Optional[Path] = None,
) -> _Transport:
    """The SDK transport for `config`, given resolved env/headers. Every HTTP request goes
    through the URL guard; a local server gets the host's network settings and its own env."""
    if config.transport == MCPTransport.STDIO:
        try:
            command, args = resolve_stdio_launch(config)
        except StdioPolicyError as e:
            raise MCPLaunchRefusedError(str(e)) from e
        # Credential records saved before env-name validation existed can still carry these.
        if rejected := rejected_env_names(env or {}):
            raise MCPLaunchRefusedError(
                f"MCP instance {config.id} has env vars that are not allowed for STDIO servers: {rejected}"
            )
        params = StdioServerParameters(
            command=command,
            args=args,
            # A server's own values (its credentials) win over the host's.
            env={**inherited_network_env(), **(env or {})},
        )
        if stderr_log_file is None:
            return _Transport(stdio_client(params))
        # The subprocess writes to it through its descriptor: it has to be a real file.
        stderr = open(stderr_log_file, "w", encoding="utf-8", errors="replace")
        return _Transport(stdio_client(params, errlog=stderr), stderr=stderr)

    if config.transport in (MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP):
        if not config.url:
            raise MCPConnectionError(f"MCP instance {config.id} is {config.transport.value} but has no url configured")
        allow_private = private_network_allowed(config)
        read_timeout = http_read_timeout(config)
        if config.transport == MCPTransport.STREAMABLE_HTTP:
            client = guarded_mcp_http_client(
                config.url, allow_private=allow_private, headers=dict(headers or {}), read_timeout=read_timeout,
            )
            return _Transport(
                streamable_http_client(config.url, http_client=client), http_clients=[client], owned_http_client=client,
            )
        transport = _Transport(None)
        make_client = guarded_mcp_http_client_factory(config.url, allow_private=allow_private, read_timeout=read_timeout)

        def _sse_http_client(**kwargs: Any) -> "httpx2.AsyncClient":  # noqa: ANN401
            client = make_client(**kwargs)
            transport.http_clients.append(client)
            return client

        transport.streams = sse_client(
            config.url, headers=dict(headers or {}), timeout=MCP_DEFAULT_TIMEOUT,
            sse_read_timeout=read_timeout, httpx_client_factory=_sse_http_client,
        )
        return transport

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


def _describe_error(exc: BaseException) -> str:
    """Caller-safe text for an SDK/transport failure. An HTTP status error prints the full
    request URL, which for some servers carries an API key in the query string."""
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _describe_error(exc.exceptions[0])
    if isinstance(exc, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
        response = exc.response
        return f"HTTP {response.status_code} {response.reason_phrase} from {redact_url(str(exc.request.url))}"
    return redact_urls_in_text(str(exc)) or type(exc).__name__


def _keep_results_that_miss_their_schema(client: Client) -> None:
    """The SDK checks a result against the tool's declared `outputSchema` after the tool ran,
    and on a mismatch (or a failed listing it makes to check) raises, throwing the result away;
    the model then usually runs the tool again. Keep the result and log the mismatch instead.

    `validate_tool_result` is looked up on the session, so replacing it there is enough; a test
    pins it to the installed SDK. If a release drops it, validation simply stays strict."""
    session = client.session
    validate = getattr(session, "validate_tool_result", None)
    if validate is None:
        logger.debug("MCP SDK has no output validation hook; tool results stay strictly validated")
        return

    async def _validate_leniently(name: str, result: Any) -> None:  # noqa: ANN401
        try:
            await validate(name, result)
        except Exception as e:
            logger.warning(f"MCP tool {name} returned output its declared schema doesn't allow; kept as is: {e}")

    session.validate_tool_result = _validate_leniently


# HTTP servers found to speak only the initialize handshake, keyed by instance and URL: their
# later sessions skip the SDK's version probe, a round trip (up to 10 s of the connect budget
# for a server that ignores it). A modern server's probe is its handshake, so it isn't skipped.
_initialize_only: dict[str, float] = {}


def _handshake_key(config: MCPServerConfig) -> Optional[str]:
    if config.transport != MCPTransport.STREAMABLE_HTTP or not config.url:
        return None
    return f"{config.id}|{config.url}"


def _remembered_initialize_only(config: MCPServerConfig) -> bool:
    key = _handshake_key(config)
    seen_at = _initialize_only.get(key) if key else None
    return seen_at is not None and time.monotonic() - seen_at < _REMEMBERED_HANDSHAKE_SECONDS


def _client_mode(config: MCPServerConfig) -> str:
    """Local servers and SSE always use the initialize handshake: an older local server can
    exit on an unknown first request, and SSE predates the version probe."""
    if config.transport != MCPTransport.STREAMABLE_HTTP or _remembered_initialize_only(config):
        return "legacy"
    return "auto"


def _remember_handshake(config: MCPServerConfig, client: Client, mode: str) -> None:
    """Only a probe's answer is remembered: a session that skipped the probe says nothing new, and
    counting it would keep the memory from ever running out while the server is in use."""
    key = _handshake_key(config)
    if key is None or mode != "auto":
        return
    if client.session.initialize_result is not None:
        _initialize_only[key] = time.monotonic()
    else:
        _initialize_only.pop(key, None)


def _forget_handshake(config: MCPServerConfig) -> None:
    key = _handshake_key(config)
    if key:
        _initialize_only.pop(key, None)


def _instructions_of(client: Client) -> Optional[str]:
    text = client.instructions
    return text.strip() if isinstance(text, str) and text.strip() else None


# What the SDK says when it gave up on a request itself, as opposed to an error a server sent.
_SDK_CONNECTION_CLOSED = "Connection closed"
_SDK_STREAM_LOST = (
    "SSE stream ended without a response",
    "SSE stream failed:",
    "SSE stream ended and reconnection attempts were exhausted",
)
# The SDK's stand-in text for an HTTP error without a JSON-RPC body, and its other own messages.
_SDK_STAND_INS = frozenset({"Server returned an error response", "Not Found", "Session terminated"})
_SDK_MESSAGE_PREFIXES = (
    _SDK_CONNECTION_CLOSED,
    *_SDK_STREAM_LOST,
    "Redirect to ",
    "server answered a request with 202 Accepted",
    "Unexpected content type:",
    "Failed to parse JSON response:",
    "Failed to parse SSE message:",
)


def _is_sdk_message(error: MCPError) -> bool:
    message = error.message or ""
    return (
        message in _SDK_STAND_INS
        or message.startswith(_SDK_MESSAGE_PREFIXES)
        or (error.code == REQUEST_TIMEOUT and message.startswith("Request ") and message.endswith(" timed out"))
    )


def _connection_closed(client: Client) -> bool:
    """Whether the SDK's session has seen its connection end: a local server that exited, an SSE
    stream that ended. The `Client` stays entered after that, and only its dispatcher knows; a
    request sent then fails before it is written. Private SDK state, pinned by a test; if it
    moves, this reads False and such a request is reported as interrupted instead."""
    dispatcher = getattr(getattr(client, "_session", None), "_dispatcher", None)
    return getattr(dispatcher, "_closed", False) is True


def _is_sdk_timeout(error: MCPError, method: str) -> bool:
    return error.code == REQUEST_TIMEOUT and error.message == f"Request {method!r} timed out"


def _is_refused_redirect(error: MCPError) -> bool:
    return error.message.startswith("Redirect to ") and "not followed" in error.message


_REFUSED_REDIRECT = (
    "The MCP server redirected the request to a different address, which isn't followed. "
    "If that address is the intended server, use it as the server URL."
)


def _http_failure_message(status: int, url: str, server_said: str = "") -> str:
    if status == 404:
        return (
            f"The MCP server answered 404 Not Found at {url}. This usually means the MCP endpoint "
            "isn't enabled/available for this account (wrong URL, a feature/beta flag that needs "
            "turning on, or a plan/entitlement requirement not met) — check the connector's "
            "prerequisites and the configured server URL, then retry."
        )
    said = f": {server_said}" if server_said else ""
    return f"The MCP server answered HTTP {status} at {url}{said}"


def _server_said(error: BaseException) -> str:
    """A JSON-RPC error's own message, when the server sent one."""
    if not isinstance(error, MCPError) or _is_sdk_message(error):
        return ""
    return redact_urls_in_text(error.message.splitlines()[0] if error.message else "")[:300]


def _wire_failure(record: wire.WireRecord, *, what: str) -> Optional[MCPConnectionError]:
    """What an operation's own requests say went wrong, or None when they say nothing."""
    if record.unsent is not None:
        if isinstance(record.unsent, MCPUrlBlockedError):
            return record.unsent
        return MCPRequestNotSentError(f"Couldn't reach the MCP server: {_describe_error(record.unsent)}")
    if record.lost is not None:
        return MCPRequestLostError(f"The connection to the MCP server failed during {what}: {_describe_error(record.lost)}")
    status = record.error_status
    if status is None:
        return None
    if status == 404 and record.sent_session_id:
        return MCPSessionExpiredError(f"The MCP server no longer knows this session (HTTP 404 during {what}).")
    return MCPHttpStatusError(status, f"The MCP server answered HTTP {status} during {what}.", challenge=record.challenges.get(status))


class _SessionRunner:
    """Holds one SDK `Client` open in a task of its own for as long as the session lasts.

    The SDK's task group has to be entered and left in the same task, and a transport failure
    inside it cancels that task: in the chat turn's task it would cancel the turn. Other tasks
    call the client concurrently, which the SDK supports. Connecting is bounded by a cancel
    scope this task owns, and closing goes through the same scope, never a native
    `task.cancel()`, which could cut the SDK's shielded shutdown of a local server."""

    def __init__(self, client: Client, transport: _Transport, *, connect_seconds: float, name: str) -> None:
        self.client = client
        self.transport = transport
        self.failure: Optional[BaseException] = None
        # The requests made in this task: the handshake, the stream listener and the close.
        self.connect_record = wire.WireRecord()
        self._connect_seconds = connect_seconds
        self._name = name
        self._scope: Optional[anyio.CancelScope] = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._finished = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    @property
    def running(self) -> bool:
        return self._ready.is_set() and not self._finished.is_set()

    async def start(self) -> None:
        """Returns once the session is ready; raises what ended it otherwise."""
        self._task = asyncio.create_task(self._run(), name=self._name)
        waiters = [asyncio.ensure_future(self._ready.wait()), asyncio.ensure_future(self._finished.wait())]
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            await self.stop()
            raise
        finally:
            for waiter in waiters:
                waiter.cancel()
        if not self._ready.is_set():
            raise self.failure or MCPConnectionError("The MCP session ended before it was ready")

    async def _run(self) -> None:
        try:
            if self._closing.is_set():
                return
            with anyio.CancelScope() as scope:
                self._scope = scope
                scope.deadline = anyio.current_time() + self._connect_seconds
                with wire.recording() as record:
                    self.connect_record = record
                    async with self.client:
                        scope.deadline = math.inf
                        self._ready.set()
                        await self._closing.wait()
            if scope.cancelled_caught and not self._ready.is_set() and not self._closing.is_set():
                self.failure = TimeoutError(f"Timed out connecting after {self._connect_seconds:g}s")
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # a group can hold a cancellation; this task must still end quietly
            self.failure = e
        finally:
            await self.transport.aclose()
            self._finished.set()

    async def stop(self) -> None:
        self._closing.set()
        task = self._task
        if task is None or task.done():
            return
        if not self._ready.is_set() and self._scope is not None:
            self._scope.cancel()
        _, pending = await asyncio.wait({task}, timeout=_CLOSE_TIMEOUT_SECONDS)
        if pending and self._scope is not None:
            logger.warning(f"MCP session {self._name} didn't close within {_CLOSE_TIMEOUT_SECONDS:g}s; cancelling it")
            self._scope.cancel()
            await asyncio.wait(pending, timeout=_CLOSE_TIMEOUT_SECONDS)


class MCPListingTimeoutError(TimeoutError):
    """The server didn't list its tools in time: the listing's own deadline, not a call's."""


class MCPCallTooLongError(TimeoutError):
    """A call the server kept alive with progress ran past `max_call_seconds`."""

    def __init__(self, message: str, *, limit_seconds: float) -> None:
        super().__init__(message)
        self.limit_seconds = limit_seconds


@dataclass(frozen=True)
class ToolListing:
    """A server's tools and what the tool cache keeps with them."""

    tools: list[Any]
    instructions: Optional[str] = None
    # The server's own cache hints (`ttlMs`, `cacheScope`), from a 2026-07-28 session only. That
    # wire always carries both; a server that set nothing sends 0 and private (`tool_cache`
    # keeps such a list briefly).
    ttl_seconds: Optional[float] = None
    public: bool = False


def _cache_hints(client: Client, page: Any) -> tuple[Optional[float], bool]:  # noqa: ANN401
    if getattr(client, "protocol_version", None) not in MODERN_PROTOCOL_VERSIONS:
        return None, False
    sent = getattr(page, "model_fields_set", set())
    ttl_ms = getattr(page, "ttl_ms", None) if "ttl_ms" in sent else None
    public = "cache_scope" in sent and getattr(page, "cache_scope", None) == "public"
    return (ttl_ms / 1000 if isinstance(ttl_ms, int) else None), public


@dataclass
class _Session:
    runner: _SessionRunner
    stderr_path: Optional[Path]
    # A failed request ended it even though the runner is still up (an SSE writer that died).
    lost: bool = False

    @property
    def client(self) -> Client:
        return self.runner.client

    @property
    def usable(self) -> bool:
        return self.runner.running and not self.lost and not _connection_closed(self.client)


class MCPClientManager:
    """A connection to one MCP server instance: one-shot (`connect()`, `list_tools()`,
    `call_tool()`) or a long-lived session (`open()` … `aclose()`) for a chat turn."""

    def __init__(
        self,
        config: MCPServerConfig,
        env: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.config = config
        self.env = env or {}
        self.headers = headers or {}
        self._session: Optional[_Session] = None
        self._instructions: Optional[str] = None
        # The server said, during this manager's session, that its tools changed.
        self.tools_changed = False

    async def _on_server_message(self, message: Any) -> None:  # noqa: ANN401
        """Runs in the SDK's read loop: only notes what it needs. It also gets transport errors."""
        if isinstance(message, ToolListChangedNotification):
            self.tools_changed = True

    async def _check_url(self) -> None:
        """Fail with a readable error before anything is sent. The transport's guard still
        covers redirects and DNS."""
        if self.config.transport in (MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP) and self.config.url:
            await check_mcp_url(self.config.url, allow_private=private_network_allowed(self.config))

    async def _start_session(self) -> _Session:
        await self._check_url()
        mode = _client_mode(self.config)
        try:
            return await self._start_session_in(mode)
        except MCPConnectionError as e:
            # A server remembered as initialize-only may have moved on: probe it again, once.
            if mode == "auto" or not _remembered_initialize_only(self.config) or not _worth_renegotiating(e):
                raise
            _forget_handshake(self.config)
            logger.info(f"MCP server {self.config.id} refused the remembered handshake; probing its version again")
            return await self._start_session_in("auto")

    async def _start_session_in(self, mode: str) -> _Session:
        stderr_path = _new_stderr_capture_path(self.config)
        transport: Optional[_Transport] = None
        try:
            transport = build_transport(self.config, env=self.env, headers=self.headers, stderr_log_file=stderr_path)
            # The SDK's response cache stays off: tool lists are cached across sessions elsewhere.
            client = Client(
                transport.streams, mode=mode, read_timeout_seconds=call_timeout(self.config), cache=None,
                message_handler=self._on_server_message, client_info=CLIENT_INFO,
            )
        except BaseException:
            if transport is not None and transport.stderr is not None:
                transport.stderr.close()
            _cleanup_stderr_capture_path(stderr_path)
            raise
        runner = _SessionRunner(
            client, transport, connect_seconds=connect_timeout(self.config), name=f"mcp-session-{self.config.id}",
        )
        try:
            await runner.start()
        except asyncio.CancelledError:
            _cleanup_stderr_capture_path(stderr_path)
            raise
        except (Exception, BaseExceptionGroup) as e:
            stderr_tail = _read_stderr_tail(stderr_path)
            await runner.stop()
            _cleanup_stderr_capture_path(stderr_path)
            error = self._connect_error(e, runner.connect_record, stderr_tail)
            logger.error(f"Failed to connect to MCP server {self.config.id} ({self.config.name}): {error.detail}")
            # A connection error raised inside the SDK keeps its own cause.
            raise error from (error.__cause__ if error is first_leaf(e) else e)
        _remember_handshake(self.config, client, mode)
        _keep_results_that_miss_their_schema(client)
        return _Session(runner, stderr_path)

    def _connect_error(self, e: BaseException, record: wire.WireRecord, stderr_tail: str) -> MCPConnectionError:
        leaf = first_leaf(e)
        url = redact_url(self.config.url) if self.config.url else ""
        if isinstance(leaf, MCPConnectionError):
            error = leaf
        elif isinstance(leaf, TimeoutError):
            # Before the record: a version probe the server refused leaves an error status there.
            error = MCPConnectionError(f"Timed out connecting to the MCP server after {connect_timeout(self.config):g}s")
        elif (from_wire := _wire_failure(record, what="the handshake")) is not None:
            error = from_wire
            if isinstance(from_wire, MCPHttpStatusError):
                error = MCPHttpStatusError(
                    from_wire.status_code, _http_failure_message(from_wire.status_code, url, _server_said(leaf)),
                    challenge=from_wire.challenge,
                )
        elif isinstance(leaf, MCPError) and _is_refused_redirect(leaf):
            logger.warning(f"MCP server {self.config.id} redirected elsewhere: {redact_urls_in_text(leaf.message)}")
            error = MCPUrlBlockedError(_REFUSED_REDIRECT)
        elif isinstance(leaf, httpx2.HTTPStatusError) and leaf.response.is_redirect:
            error = MCPUrlBlockedError(_REFUSED_REDIRECT)
        else:
            error = MCPConnectionError(_describe_error(leaf))
        if stderr_tail and not error.stderr_tail:
            error.stderr_tail = stderr_tail
        return error

    async def _close_session(self, session: _Session) -> None:
        try:
            await session.runner.stop()
        except Exception as e:
            logger.debug(f"Error closing MCP session for {self.config.id} ({self.config.name}): {e}")
        finally:
            _cleanup_stderr_capture_path(session.stderr_path)

    @asynccontextmanager
    async def _one_shot(self) -> AsyncIterator[_Session]:
        session = await self._start_session()
        try:
            yield session
        finally:
            # Shielded: a caller's deadline can cancel the work, not the close.
            await asyncio.shield(self._close_session(session))

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Client]:
        """A one-shot session."""
        async with self._one_shot() as session:
            yield session.client

    async def list_tools(self) -> list[Any]:
        return (await self.fetch_tool_listing()).tools

    async def fetch_tool_listing(self) -> ToolListing:
        """A one-shot listing with the server's instructions and cache hints."""
        async with self._one_shot() as session:
            return await self._list_all(session)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
        """Returns the protocol `CallToolResult`."""
        async with self._one_shot() as session:
            return await self._call(session, tool_name, arguments)

    @property
    def server_instructions(self) -> Optional[str]:
        """The open session's instructions: the server's own guidance on using its tools.
        None before `open()` or when the server sent none."""
        return self._instructions

    async def open(self) -> Client:
        """Opens a session for reuse across `call_tool_in_session()` calls. Idempotent;
        callers MUST `aclose()` when done with this instance."""
        if self._session is None:
            self._session = await self._start_session()
            self._instructions = _instructions_of(self._session.client)
        return self._session.client

    @property
    def is_open(self) -> bool:
        """Whether the session `open()` established can still carry a request."""
        return self._session is not None and self._session.usable

    def update_headers(self, headers: dict[str, str]) -> None:
        """Headers later requests send (a refreshed token), on the open session too."""
        self.headers.update(headers)
        if self._session is not None:
            self._session.runner.transport.set_headers(headers)

    async def list_tools_in_session(self) -> list[Any]:
        """Lists tools on the session `open()` established. Listing on the session that then
        runs the calls also gives the SDK each tool's output schema for checking results."""
        return (await self.fetch_tool_listing_in_session()).tools

    async def fetch_tool_listing_in_session(self) -> ToolListing:
        """`list_tools_in_session` with the server's instructions and cache hints."""
        return await self._list_all(self._live_session())

    async def call_tool_in_session(
        self, tool_name: str, arguments: dict[str, Any], *, on_progress: Optional[ProgressFnT] = None,
    ) -> Any:  # noqa: ANN401
        """Calls a tool on the session `open()` established and returns the protocol
        `CallToolResult`. `on_progress` hears the server's progress reports; it must not block.

        Raises MCPConnectionError if no session is open."""
        session = self._live_session()
        try:
            return await self._call(session, tool_name, arguments, on_progress)
        except Exception as e:
            # The stderr tail is for operators only: the error travels on to the model and
            # the tool card, and a server's stderr can echo its environment.
            if stderr_tail := _read_stderr_tail(session.stderr_path):
                logger.warning(
                    f"MCP tool call {tool_name} on {self.config.id} ({self.config.name}) failed: "
                    f"{_describe_error(e)} | subprocess stderr: {stderr_tail}"
                )
            raise

    def _live_session(self) -> _Session:
        session = self._session
        if session is None:
            raise MCPConnectionError(f"MCP session for {self.config.id} ({self.config.name}) is not open")
        if not session.usable:
            raise MCPRequestNotSentError("The connection to the MCP server had already closed.")
        return session

    async def _list_all(self, session: _Session) -> ToolListing:
        tools: list[Any] = []
        cursor: Optional[str] = None
        hints: tuple[Optional[float], bool] = (None, False)
        with wire.recording() as record:
            try:
                async with asyncio.timeout(LIST_TOOLS_TIMEOUT_SECONDS):
                    for page_number in range(_MAX_TOOL_PAGES):
                        page = await session.client.list_tools(cursor=cursor)
                        if page_number == 0:
                            hints = _cache_hints(session.client, page)
                        tools.extend(page.tools)
                        cursor = page.next_cursor
                        if not cursor:
                            break
                    else:
                        # Once per server and process: every turn lists it again.
                        if self.config.id not in _truncation_warned:
                            _truncation_warned.add(self.config.id)
                            logger.warning(
                                "MCP server %s (%s) lists more than %d pages of tools; only the first %d tools are used",
                                self.config.id, self.config.name, _MAX_TOOL_PAGES, len(tools),
                            )
                    return ToolListing(
                        tools=tools, instructions=_instructions_of(session.client), ttl_seconds=hints[0], public=hints[1],
                    )
            except TimeoutError:
                raise MCPListingTimeoutError(f"The MCP server didn't list its tools within {LIST_TOOLS_TIMEOUT_SECONDS:g}s") from None
            except MCPError as e:
                # A listing changes nothing on the server, so every failure is its own to retry.
                error = self._operation_error(
                    session, e, record, method="tools/list", what="the tool listing", may_have_run_once_taken=False,
                )
                if error is None:
                    raise
                raise error from e
            except RuntimeError as e:
                if session.runner.running:
                    raise
                raise self._closed_under(session) from e

    async def _call(
        self, session: _Session, tool_name: str, arguments: dict[str, Any], on_progress: Optional[ProgressFnT] = None,
    ) -> Any:  # noqa: ANN401
        """The call timeout bounds each wait for the answer or for progress: a progress report
        restarts it, up to `max_call_seconds` in all, as the MCP spec has clients do."""
        seconds = call_timeout(self.config)
        limit = max_call_seconds(self.config)
        loop = asyncio.get_running_loop()
        started = loop.time()
        finished = capped = False

        async def progress(value: float, total: Optional[float], message: Optional[str]) -> None:
            nonlocal capped
            # The SDK runs this in a task of its own, so it can come after the call ended or timed out.
            if finished or clock.expired():
                return
            deadline = loop.time() + seconds
            if deadline > started + limit:
                deadline, capped = started + limit, True
            clock.reschedule(deadline)
            if on_progress is not None:
                try:
                    await on_progress(value, total, message)
                except Exception:
                    logger.debug("Reporting an MCP tool call's progress failed", exc_info=True)

        with wire.recording() as record:
            try:
                async with asyncio.timeout(seconds) as clock:
                    try:
                        return await session.client.call_tool(
                            tool_name, arguments, read_timeout_seconds=limit + _OUTER_TIMEOUT_MARGIN_SECONDS,
                            progress_callback=progress,
                        )
                    finally:
                        finished = True
            except TimeoutError:
                # Our clock ended the call, but what the wire saw still counts.
                self._failed_on_the_wire(session, record)
                if capped:
                    raise MCPCallTooLongError(
                        f"The MCP tool call was still running after {limit:g}s, the limit for one call", limit_seconds=limit,
                    ) from None
                raise TimeoutError(f"The MCP tool call didn't finish within {seconds:g}s") from None
            except MCPError as e:
                error = self._operation_error(
                    session, e, record, method="tools/call", what=f"the call to {tool_name}", may_have_run_once_taken=True,
                )
                if error is None:
                    raise
                raise error from e
            except RuntimeError as e:
                if session.runner.running:
                    raise
                raise self._closed_under(session) from e

    def _failed_on_the_wire(self, session: _Session, record: wire.WireRecord) -> bool:
        failed = record.unsent is not None or record.lost is not None or record.error_status is not None
        # A failed SSE POST ends that transport's writer without a word; nothing more can be sent.
        if failed and self.config.transport == MCPTransport.SSE:
            session.lost = True
        return failed

    def _operation_error(
        self, session: _Session, error: MCPError, record: wire.WireRecord, *, method: str, what: str,
        may_have_run_once_taken: bool,
    ) -> Optional[BaseException]:
        """An `MCPError` from a listing or a call, as what actually happened; None when it is
        the server's own error, to be raised as it is.

        With `may_have_run_once_taken`, a failure after the server took the request (a dropped
        stream's resumption refused, unreachable or answered 401) is reported as one that may
        have run: the request itself went through."""
        status = record.error_status
        failed_on_the_wire = self._failed_on_the_wire(session, record)
        forgot_the_session = (
            status == 404 and record.sent_session_id and error.code != METHOD_NOT_FOUND
            and not (may_have_run_once_taken and record.accepted)
        )
        if status is not None and status not in (401, 403) and not forgot_the_session and _server_said(error):
            # The server's own JSON-RPC error at an error status (2.x servers send invalid
            # params as 400): its message is what the model needs.
            return None
        if may_have_run_once_taken and record.accepted and failed_on_the_wire:
            if isinstance(record.lost, httpx2.TimeoutException):
                return TimeoutError(f"The MCP server didn't answer {what} in time")
            return MCPRequestLostError(f"The connection to the MCP server failed after it took {what}.")
        if (from_wire := _wire_failure(record, what=what)) is not None:
            if isinstance(from_wire, MCPSessionExpiredError):
                session.lost = True
            if isinstance(from_wire, MCPRequestLostError) and isinstance(record.lost, httpx2.TimeoutException):
                return TimeoutError(f"The MCP server didn't answer {what} in time")
            return from_wire
        if _is_sdk_timeout(error, method):
            return TimeoutError(f"The MCP server didn't answer {what} within {call_timeout(self.config):g}s")
        if _is_refused_redirect(error):
            logger.warning(f"MCP server {self.config.id} redirected elsewhere: {redact_urls_in_text(error.message)}")
            return MCPUrlBlockedError(_REFUSED_REDIRECT)
        if error.code == CONNECTION_CLOSED and error.message == _SDK_CONNECTION_CLOSED:
            session.lost = True
            if session.runner.failure is not None:
                logger.warning(
                    f"MCP session for {self.config.id} ({self.config.name}) ended: {_describe_error(session.runner.failure)}"
                )
            return MCPConnectionLostError("The connection to the MCP server closed.")
        if error.code == CONNECTION_CLOSED and error.message.startswith(_SDK_STREAM_LOST):
            return MCPRequestLostError(f"The MCP server's response stream ended before it answered {what}.")
        return None

    @staticmethod
    def _closed_under(session: _Session) -> BaseException:
        """What the SDK's refusal of a request on a client whose session has ended means."""
        session.lost = True
        return MCPRequestNotSentError("The connection to the MCP server had already closed.")

    async def aclose(self) -> None:
        """Closes the session opened by `open()`. A no-op if never opened."""
        session, self._session = self._session, None
        if session is not None:
            await self._close_session(session)


def _worth_renegotiating(error: MCPConnectionError) -> bool:
    """Whether a failed connect might be the handshake's fault rather than the server's being
    down, refused or rejecting the credentials."""
    if isinstance(error, (MCPRequestNotSentError, MCPUrlBlockedError, MCPLaunchRefusedError)):
        return False
    return not is_http_unauthorized(error)

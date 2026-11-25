"""``SandboxBroker`` over the broker's HTTP API (``wire``): what callers without a runtime socket use."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from typing import TYPE_CHECKING, cast

import aiohttp

from harness_core.deadline import CONNECT_TIMEOUT_S, bounded_read
from harness_core.region import REGION_LABEL
from harness_core.sandbox.errors import (
    BrokerUnauthorized,
    BrokerUnavailable,
    InvalidSandboxRequest,
    SandboxError,
    SandboxNotFound,
)
from harness_core.sandbox.remote import wire
from harness_core.sandbox.streams import ChunkChannel
from harness_core.sandbox.types import OrgSandboxErasure, RuntimeMode, is_safe_segment

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence

    from harness_core.sandbox.auth import BrokerAuth
    from harness_core.sandbox.types import BrokerHealth, LeaseRef, SandboxSpec

REQUEST_TIMEOUT_S = 120.0
# A stream (exec, port tunnel) pings the broker this often and is lost when a pong does not follow within half of
# it. A silently partitioned link (CH-12) otherwise holds every byte with no error, so a wait on the stream never ends.
HEARTBEAT_S = 10.0
# Upgrading a stream is one round trip: one that does not complete in this time is a broker that cannot be reached.
HANDSHAKE_S = CONNECT_TIMEOUT_S + 20.0


class RemoteSandboxBroker:
    """Build with :meth:`open`, which learns the broker's modes; close with :meth:`aclose`."""

    def __init__(
        self,
        base_url: str,
        auth: BrokerAuth,
        *,
        session: aiohttp.ClientSession | None = None,
        heartbeat_s: float = HEARTBEAT_S,
        handshake_s: float = HANDSHAKE_S,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
        region: str | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        # OWD-12: every sandbox this caller asks for names its region, which the broker checks against its own.
        self._region = region
        self._auth = auth
        self._session = session
        self._owns_session = session is None
        self._modes: frozenset[RuntimeMode] = frozenset()
        self._heartbeat_s = heartbeat_s
        self._handshake_s = handshake_s
        self._request_timeout_s = request_timeout_s

    @classmethod
    async def open(
        cls,
        base_url: str,
        auth: BrokerAuth,
        *,
        session: aiohttp.ClientSession | None = None,
        heartbeat_s: float = HEARTBEAT_S,
        handshake_s: float = HANDSHAKE_S,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
        region: str | None = None,
    ) -> RemoteSandboxBroker:
        broker = cls(
            base_url, auth, session=session, heartbeat_s=heartbeat_s, handshake_s=handshake_s,
            request_timeout_s=request_timeout_s, region=region,
        )
        try:
            _ = await broker.refresh_modes()
        except BaseException:
            await broker.aclose()
            raise
        return broker

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=CONNECT_TIMEOUT_S)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def url(self, path: str) -> str:
        return self._base + path

    async def headers(self) -> dict[str, str]:
        return dict(await self._auth.headers())

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object = None,
        params: Mapping[str, str] | None = None,
        data: AsyncIterator[bytes] | None = None,
    ) -> object:
        """One request; a JSON body back (``None`` for 204) or the server's typed error raised. On a run's tool
        call it is bounded by the call's remaining time too (ERR-UX)."""
        return await bounded_read("sandbox_broker", self._call(method, path, json=json, params=params, data=data))

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: object,
        params: Mapping[str, str] | None,
        data: AsyncIterator[bytes] | None,
    ) -> object:
        try:
            async with self._http().request(
                method,
                self.url(path),
                json=json,
                params=params,
                data=data,
                headers=await self.headers(),
                # A per-request timeout replaces the session's: the connect bound is restated here (ERR-UX).
                timeout=aiohttp.ClientTimeout(total=self._request_timeout_s, sock_connect=CONNECT_TIMEOUT_S),
            ) as response:
                if response.status == 204:
                    return None
                body = await _json_or_none(response)
                if response.status >= 400:
                    raise wire.error_from(body, status=response.status)
                return body
        # aiohttp's own total timeout is a TimeoutError, not a ClientError (FB-9).
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BrokerUnavailable(f"broker unreachable: {type(exc).__name__}") from exc

    async def stream(self, path: str, *, params: Mapping[str, str]) -> AsyncIterator[bytes]:
        """A streamed GET; errors before the first byte are raised here."""
        try:
            response = await self._http().get(self.url(path), params=params, headers=await self.headers())
        except aiohttp.ClientError as exc:
            raise BrokerUnavailable(f"broker unreachable: {type(exc).__name__}") from exc
        if response.status >= 400:
            body = await _json_or_none(response)
            response.release()
            raise wire.error_from(body, status=response.status)
        return _body_chunks(response)

    async def websocket(self, path: str, *, params: Mapping[str, str] | None = None) -> aiohttp.ClientWebSocketResponse:
        try:
            async with asyncio.timeout(self._handshake_s):
                return await self._http().ws_connect(
                    self.url(path), params=params, headers=await self.headers(), heartbeat=self._heartbeat_s
                )
        except aiohttp.WSServerHandshakeError as exc:
            if exc.status == 401:
                raise BrokerUnauthorized("credential missing or not accepted") from exc
            raise SandboxError(f"broker refused the stream: HTTP {exc.status}") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BrokerUnavailable(f"broker unreachable: {type(exc).__name__}") from exc

    def modes(self) -> frozenset[RuntimeMode]:
        return self._modes

    async def refresh_modes(self) -> frozenset[RuntimeMode]:
        self._modes = wire.parse_modes(await self.call("GET", wire.MODES))
        return self._modes

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> RemoteSandboxHandle:
        if self._region is not None and REGION_LABEL not in spec.labels:
            spec = dataclasses.replace(spec, labels={**spec.labels, REGION_LABEL: self._region})
        body = await self.call("POST", wire.SANDBOXES, json=wire.acquire_body(spec, lease, idempotency_key))
        fields = cast("dict[str, object]", body) if isinstance(body, dict) else {}
        sandbox_id, mode = fields.get("id"), fields.get("mode")
        if not isinstance(sandbox_id, str) or not isinstance(mode, str):
            raise SandboxError("malformed acquire response")
        return RemoteSandboxHandle(
            self, sandbox_id, RuntimeMode(mode), wire.parse_verified_binaries(fields.get("verifiedBinaries"))
        )

    async def release(self, handle: object, *, keep_warm_s: int = 0) -> None:
        sandbox_id = getattr(handle, "id", None)
        if not isinstance(sandbox_id, str):
            raise SandboxNotFound("not a sandbox handle")
        _ = await self.call("DELETE", wire.sandbox_path(sandbox_id), params={"keep_warm_s": str(keep_warm_s)})

    async def reap(self, *, labels: Mapping[str, str], older_than_s: int) -> int:
        body = await self.call("POST", wire.REAP, json={"labels": dict(labels), "older_than_s": older_than_s})
        reaped = cast("dict[str, object]", body).get("reaped") if isinstance(body, dict) else None
        if not isinstance(reaped, int):
            raise SandboxError("malformed reap response")
        return reaped

    async def erase_session(self, *, org_id: str, session_id: str) -> int:
        if not (is_safe_segment(org_id) and is_safe_segment(session_id)):
            raise InvalidSandboxRequest("org and session ids must be single safe path segments")
        body = await self.call("POST", wire.ERASE_SESSION, json={"orgId": org_id, "sessionId": session_id})
        erased = cast("dict[str, object]", body).get("erased") if isinstance(body, dict) else None
        if not isinstance(erased, int):
            raise SandboxError("malformed erase response")
        return erased

    async def erase_org(self, *, org_id: str) -> OrgSandboxErasure:
        if not is_safe_segment(org_id):
            raise InvalidSandboxRequest("the org id must be a single safe path segment")
        body = await self.call("POST", wire.ERASE_ORG, json={"orgId": org_id})
        answer = cast("dict[str, object]", body) if isinstance(body, dict) else {}
        sandboxes, folders = answer.get("sandboxes"), answer.get("folders")
        if not isinstance(sandboxes, int) or not isinstance(folders, int):
            raise SandboxError("malformed erase response")
        return OrgSandboxErasure(sandboxes, folders)

    async def health(self) -> BrokerHealth:
        health = wire.HEALTH_BODY.validate_python(await self.call("GET", wire.HEALTH))
        self._modes = health.modes
        return health


async def _json_or_none(response: aiohttp.ClientResponse) -> object:
    try:
        return await response.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return None


async def _body_chunks(response: aiohttp.ClientResponse) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.content.iter_chunked(64 * 1024):
            yield chunk
    finally:
        response.release()


class RemoteSandboxHandle:
    def __init__(
        self,
        client: RemoteSandboxBroker,
        sandbox_id: str,
        mode: RuntimeMode,
        verified_binaries: Mapping[str, str] | None = None,
    ) -> None:
        self._client = client
        self._id = sandbox_id
        self._mode = mode
        self._binaries = dict(verified_binaries or {})

    @property
    def id(self) -> str:
        return self._id

    @property
    def mode(self) -> RuntimeMode:
        return self._mode

    @property
    def verified_binaries(self) -> Mapping[str, str]:
        return self._binaries

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> RemoteProcess:
        ws = await self._client.websocket(wire.sandbox_path(self._id, wire.EXEC))
        await ws.send_str(wire.control("start", argv=list(argv), env=dict(env), stdin=stdin))
        await _expect(ws, wire.OP_STARTED)
        return RemoteProcess(ws, stdin_open=stdin)

    async def connect(self, port: int) -> RemoteStream:
        ws = await self._client.websocket(wire.sandbox_path(self._id, wire.CONNECT), params={"port": str(port)})
        await _expect(ws, wire.OP_CONNECTED)
        return RemoteStream(ws)

    async def put_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        _ = await self._client.call(
            "PUT", wire.sandbox_path(self._id, wire.ARCHIVE), params={"path": dest}, data=stream
        )

    async def seed_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        _ = await self._client.call(
            "PUT", wire.sandbox_path(self._id, wire.SEED), params={"path": dest}, data=stream
        )

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        return await self._client.stream(wire.sandbox_path(self._id, wire.ARCHIVE), params={"path": src})

    async def hold_flow(self, *, run_id: str, hold: str) -> bool | None:
        """``None``: the broker cannot (no proxy behind it, the sandbox is not its own, or it predates the route and
        answers a 404 that is not one of our typed errors); the caller then tells the proxy itself."""
        try:
            body = await self._client.call(
                "POST", wire.sandbox_path(self._id, wire.FLOW_HOLD), json={"runId": run_id, "hold": hold}
            )
        except SandboxNotFound:
            return None
        except SandboxError as exc:
            if type(exc) is SandboxError:
                return None
            raise
        narrowed = cast("dict[str, object]", body).get("narrowed") if isinstance(body, dict) else None
        return narrowed if isinstance(narrowed, bool) else None

    async def memory_exhausted(self) -> bool:
        try:
            body = await self._client.call("GET", wire.sandbox_path(self._id, wire.MEMORY))
        except Exception:
            return False
        return isinstance(body, dict) and cast("dict[str, object]", body).get("exhausted") is True


def _lost(ws: aiohttp.ClientWebSocketResponse) -> BrokerUnavailable | None:
    """Why ``ws`` ended, when the link failed under it (a missed heartbeat, a reset) rather than the broker closing it."""
    exc = ws.exception()
    if exc is None:
        return None
    lost = BrokerUnavailable(f"broker link lost: {type(exc).__name__}")
    lost.__cause__ = exc
    return lost


async def _expect(ws: aiohttp.ClientWebSocketResponse, op: str) -> None:
    message = await ws.receive()
    if message.type is not aiohttp.WSMsgType.TEXT:
        _ = await ws.close()
        raise _lost(ws) or BrokerUnavailable("broker closed the stream before answering")
    reply = wire.parse_control(str(message.data))
    if reply["op"] == op:
        return
    _ = await ws.close()
    raise wire.error_from(reply, status=500) if reply["op"] == wire.OP_ERROR else SandboxError("unexpected reply")


class RemoteProcess:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse, *, stdin_open: bool) -> None:
        self._ws = ws
        self._stdin_open = stdin_open
        self._stdout = ChunkChannel()
        self._stderr = ChunkChannel()
        self._exit: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._pump = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        try:
            async for message in self._ws:
                if message.type is aiohttp.WSMsgType.BINARY:
                    data = cast("bytes", message.data)
                    target = self._stdout if data[:1] == bytes([wire.STDOUT]) else self._stderr
                    await target.put(data[1:])
                elif message.type is aiohttp.WSMsgType.TEXT:
                    reply = wire.parse_control(str(message.data))
                    if reply["op"] == wire.OP_EXIT and isinstance(reply.get("code"), int):
                        self._exit.set_result(cast("int", reply["code"]))
        finally:
            self._stdout.close()
            self._stderr.close()
            if not self._exit.done():
                self._exit.set_exception(
                    _lost(self._ws) or BrokerUnavailable("broker closed the exec stream before the exit code")
                )

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return aiter(self._stdout)

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return aiter(self._stderr)

    async def write(self, data: bytes) -> None:
        if not self._stdin_open:
            raise InvalidSandboxRequest("stdin is not open")
        async with self._sending():
            await self._ws.send_bytes(data)

    async def close_stdin(self) -> None:
        self._stdin_open = False
        async with self._sending():
            await self._ws.send_str(wire.control(wire.OP_EOF))

    async def signal(self, sig: int) -> None:
        async with self._sending():
            await self._ws.send_str(wire.control(wire.OP_SIGNAL, sig=sig))

    @contextlib.asynccontextmanager
    async def _sending(self) -> AsyncGenerator[None]:
        if self._ws.closed:
            raise _lost(self._ws) or SandboxNotFound("the process stream is closed")
        try:
            yield
        except (aiohttp.ClientError, ConnectionResetError) as exc:
            raise BrokerUnavailable(f"exec stream failed: {type(exc).__name__}") from exc

    async def wait(self, *, timeout_s: float | None = None) -> int:
        code = await asyncio.wait_for(asyncio.shield(self._exit), timeout_s)
        _ = await self._ws.close()
        with contextlib.suppress(asyncio.CancelledError):
            await self._pump
        return code


class RemoteStream:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._buffer = b""
        self._eof = False

    async def read(self, max_bytes: int = 65536) -> bytes:
        while not self._buffer and not self._eof:
            message = await self._ws.receive()
            if message.type is aiohttp.WSMsgType.BINARY:
                self._buffer = cast("bytes", message.data)
            elif message.type is aiohttp.WSMsgType.TEXT:
                self._eof = wire.parse_control(str(message.data))["op"] == wire.OP_EOF
            elif (lost := _lost(self._ws)) is not None:
                raise lost
            else:
                self._eof = True
        chunk, self._buffer = self._buffer[:max_bytes], self._buffer[max_bytes:]
        return chunk

    async def write(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def write_eof(self) -> None:
        await self._ws.send_str(wire.control(wire.OP_EOF))

    async def aclose(self) -> None:
        _ = await self._ws.close()


__all__ = ["RemoteProcess", "RemoteSandboxBroker", "RemoteSandboxHandle", "RemoteStream"]

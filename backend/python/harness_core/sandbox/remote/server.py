"""HTTP server for the sandbox broker API (``wire``), over any ``SandboxBroker``.

Every route except the liveness probe requires a credential the injected
``BrokerAuthVerifier`` accepts. Callers are trusted deployment components;
tenancy lives in the spec's opaque labels, not in the transport.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import TYPE_CHECKING, Any, cast

from aiohttp import WSMsgType, web

from harness_core.logs import HarnessLog
from harness_core.region import REGION_LABEL
from harness_core.sandbox.errors import (
    BrokerUnauthorized,
    InvalidSandboxRequest,
    SandboxError,
    SandboxNotFound,
    SandboxRefusal,
)
from harness_core.sandbox.ports import hold_sandbox_flow, memory_exhausted
from harness_core.sandbox.remote import wire
from harness_core.sandbox.types import is_safe_segment
from harness_core.telemetry import security_signals
from harness_core.telemetry.exposition import METRICS_PATH, PROMETHEUS_TEXT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from aiohttp.typedefs import Handler, Middleware

    from harness_core.sandbox.auth import BrokerAuthVerifier
    from harness_core.sandbox.ports import (
        ByteStream,
        ProcessHandle,
        SandboxBroker,
        SandboxHandle,
    )

_BROKER = web.AppKey("broker", object)
_REGION = web.AppKey("region", object)
# A sandbox asked for by a caller in another region than the broker's (OWD-12).
REGION_MISMATCH = "region_mismatch"
_LOG = HarnessLog(logging.getLogger("harness_core.sandbox.remote.server"))
_CHUNK = 64 * 1024


class _Registry:
    """Handles the server has handed out, by id; a restart forgets them and callers re-acquire."""

    def __init__(self, broker: SandboxBroker) -> None:
        self.broker = broker
        self.handles: dict[str, SandboxHandle] = {}

    def get(self, sandbox_id: str) -> SandboxHandle:
        handle = self.handles.get(sandbox_id)
        if handle is None:
            raise SandboxNotFound(f"sandbox {sandbox_id} is not known to this broker")
        return handle


def _registry(request: web.Request) -> _Registry:
    return cast("_Registry", request.app[_BROKER])


def _error_response(error: SandboxError) -> web.Response:
    return web.json_response(wire.error_body(error), status=wire.status_for(error))


def _auth_middleware(verifier: BrokerAuthVerifier) -> Middleware:
    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
        if request.path != wire.LIVENESS and not await verifier.verify(dict(request.headers), path=request.path):
            security_signals.machine_credential_refused(request.headers)
            _LOG.warning("broker.http.unauthorized", path=request.path, method=request.method)
            return _error_response(BrokerUnauthorized("credential missing or not accepted"))
        try:
            return await handler(request)
        except SandboxError as exc:
            return _error_response(exc)
        except web.HTTPException:
            raise
        except Exception as exc:
            _LOG.error("broker.http.failed", exc=exc, path=request.path, errorType=type(exc).__name__)
            return _error_response(SandboxError("internal broker error"))

    return middleware


async def _json(request: web.Request) -> dict[str, object]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise InvalidSandboxRequest("body is not JSON") from exc
    if not isinstance(body, dict):
        raise InvalidSandboxRequest("body must be a JSON object")
    return cast("dict[str, object]", body)


def _int_query(request: web.Request, name: str, default: int | None = None) -> int:
    raw = request.query.get(name)
    if raw is None:
        if default is None:
            raise InvalidSandboxRequest(f"query parameter {name} is required")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidSandboxRequest(f"query parameter {name} must be an integer") from exc


def _path_query(request: web.Request) -> str:
    path = request.query.get("path", "")
    if not path.startswith("/"):
        raise InvalidSandboxRequest("query parameter path must be absolute")
    return path


async def _liveness(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _health(request: web.Request) -> web.Response:
    health = await _registry(request).broker.health()
    return web.json_response(wire.HEALTH_BODY.dump_python(health, mode="json"))


async def _modes(request: web.Request) -> web.Response:
    return web.json_response(wire.modes_body(_registry(request).broker.modes()))


async def _acquire(request: web.Request) -> web.Response:
    body = await _json(request)
    key = body.get("idempotency_key")
    if not isinstance(key, str) or not key:
        raise InvalidSandboxRequest("idempotency_key is required")
    try:
        spec = wire.SPEC.validate_python(body.get("spec"))
        lease = wire.LEASE.validate_python(body.get("lease"))
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed spec or lease: {type(exc).__name__}") from exc
    region = request.app[_REGION]
    if region is not None and spec.labels.get(REGION_LABEL) != region:
        # Placement (OWD-12): a caller that names no region, or another one, gets no sandbox here.
        _LOG.warning("broker.region_refused", brokerRegion=region, callerRegion=spec.labels.get(REGION_LABEL))
        raise SandboxRefusal("the sandbox's region is not this broker's", reason=REGION_MISMATCH)
    registry = _registry(request)
    handle = await registry.broker.acquire(spec, lease=lease, idempotency_key=key)
    registry.handles[handle.id] = handle
    return web.json_response(wire.acquired_body(handle.id, handle.mode, handle.verified_binaries))


async def _release(request: web.Request) -> web.Response:
    registry = _registry(request)
    sandbox_id = request.match_info["sandbox_id"]
    keep_warm_s = _int_query(request, "keep_warm_s", 0)
    handle = registry.handles.get(sandbox_id)
    if handle is not None:
        await registry.broker.release(handle, keep_warm_s=keep_warm_s)
        if keep_warm_s <= 0:
            del registry.handles[sandbox_id]
    return web.Response(status=204)


async def _reap(request: web.Request) -> web.Response:
    body = await _json(request)
    labels, older = body.get("labels"), body.get("older_than_s")
    if not isinstance(labels, dict) or not isinstance(older, int):
        raise InvalidSandboxRequest("labels (object) and older_than_s (integer) are required")
    typed = {str(k): str(v) for k, v in cast("dict[object, object]", labels).items()}
    reaped = await _registry(request).broker.reap(labels=typed, older_than_s=older)
    return web.json_response({"reaped": reaped})


async def _erase_session(request: web.Request) -> web.Response:
    body = await _json(request)
    org_id, session_id = body.get("orgId"), body.get("sessionId")
    if not isinstance(org_id, str) or not isinstance(session_id, str):
        raise InvalidSandboxRequest("orgId and sessionId (strings) are required")
    broker = _registry(request).broker
    erase = getattr(broker, "erase_session", None)
    # A broker without persistent folders has nothing of a session to erase.
    erased = await erase(org_id=org_id, session_id=session_id) if erase is not None else 0
    return web.json_response({"erased": erased})


async def _erase_org(request: web.Request) -> web.Response:
    body = await _json(request)
    org_id = body.get("orgId")
    if not isinstance(org_id, str) or not is_safe_segment(org_id):
        raise InvalidSandboxRequest("orgId (one safe path segment) is required")
    broker = _registry(request).broker
    erase = getattr(broker, "erase_org", None)
    if erase is not None:
        erased = await erase(org_id=org_id)
        return web.json_response({"sandboxes": erased.sandboxes, "folders": erased.folders})
    # A broker without persistent folders has only the org's sandboxes to end.
    return web.json_response({"sandboxes": await broker.reap(labels={"orgId": org_id}, older_than_s=0), "folders": 0})


async def _put_archive(request: web.Request) -> web.Response:
    handle = _registry(request).get(request.match_info["sandbox_id"])
    await handle.put_tar(_path_query(request), request.content.iter_chunked(_CHUNK))
    return web.Response(status=204)


async def _seed_archive(request: web.Request) -> web.Response:
    handle = _registry(request).get(request.match_info["sandbox_id"])
    await handle.seed_tar(_path_query(request), request.content.iter_chunked(_CHUNK))
    return web.Response(status=204)


async def _get_archive(request: web.Request) -> web.StreamResponse:
    handle = _registry(request).get(request.match_info["sandbox_id"])
    chunks = await handle.get_tar(_path_query(request))
    response = web.StreamResponse(headers={"Content-Type": "application/x-tar"})
    _ = await response.prepare(request)
    async for chunk in chunks:
        await response.write(chunk)
    await response.write_eof()
    return response


async def _memory(request: web.Request) -> web.Response:
    handle = _registry(request).get(request.match_info["sandbox_id"])
    return web.json_response({"exhausted": await memory_exhausted(handle)})


async def _flow_hold(request: web.Request) -> web.Response:
    """SEC-A-02 through the broker (R38EGRESS-FU-1): ``held``/``null`` (this broker cannot)."""
    try:
        raw: object = await request.json()
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed flow hold: {type(exc).__name__}") from exc
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    run_id, hold = body.get("runId"), body.get("hold")
    if set(body) != {"runId", "hold"} or not isinstance(run_id, str) or not run_id or hold not in ("held", "forbidden"):
        raise InvalidSandboxRequest("a flow hold names its run and a hold of held or forbidden")
    handle = _registry(request).get(request.match_info["sandbox_id"])
    return web.json_response({"narrowed": await hold_sandbox_flow(handle, run_id=run_id, hold=cast("str", hold))})


async def _open_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30.0)
    _ = await ws.prepare(request)
    return ws


async def _send_error(ws: web.WebSocketResponse, error: SandboxError) -> None:
    await ws.send_str(wire.control(wire.OP_ERROR, **wire.error_body(error)))
    _ = await ws.close()


async def _exec(request: web.Request) -> web.WebSocketResponse:
    ws = await _open_ws(request)
    try:
        handle = _registry(request).get(request.match_info["sandbox_id"])
        first = await ws.receive()
        if first.type is not WSMsgType.TEXT:
            raise InvalidSandboxRequest("the first exec message must be the request")
        start = wire.parse_control(str(first.data))
        argv, env, stdin = start.get("argv"), start.get("env"), start.get("stdin")
        if not isinstance(argv, list) or not isinstance(env, dict):
            raise InvalidSandboxRequest("argv (list) and env (object) are required")
        process = await handle.exec(
            [str(a) for a in cast("list[object]", argv)],
            env={str(k): str(v) for k, v in cast("dict[object, object]", env).items()},
            stdin=bool(stdin),
        )
    except SandboxError as exc:
        await _send_error(ws, exc)
        return ws
    await ws.send_str(wire.control(wire.OP_STARTED))
    await _serve_process(ws, process, sandbox_id=handle.id)
    return ws


async def _serve_process(ws: web.WebSocketResponse, process: ProcessHandle, *, sandbox_id: str) -> None:
    async def pump(stream: AsyncIterator[bytes], channel: int) -> None:
        async for chunk in stream:
            await ws.send_bytes(bytes([channel]) + chunk)

    async def inbound() -> None:
        async for message in ws:
            if message.type is WSMsgType.BINARY:
                await process.write(cast("bytes", message.data))
            elif message.type is WSMsgType.TEXT:
                command = wire.parse_control(str(message.data))
                if command["op"] == wire.OP_EOF:
                    await process.close_stdin()
                elif command["op"] == wire.OP_SIGNAL and isinstance(command.get("sig"), int):
                    await process.signal(cast("int", command["sig"]))

    outputs = asyncio.ensure_future(
        asyncio.gather(pump(process.stdout, wire.STDOUT), pump(process.stderr, wire.STDERR))
    )
    reader = asyncio.ensure_future(inbound())
    exited = False
    try:
        _ = await asyncio.wait({outputs, reader}, return_when=asyncio.FIRST_COMPLETED)
        if not outputs.done() or outputs.exception() is not None:
            raise ConnectionResetError
        code = await process.wait()
        exited = True
        if not ws.closed:
            await ws.send_str(wire.control(wire.OP_EXIT, code=code))
            _ = await ws.close()
    except (ConnectionResetError, asyncio.CancelledError):
        # The client went away mid-run; a process nobody can see or stop must not outlive it.
        _LOG.info("broker.exec.client_gone", sandboxId=sandbox_id, reason="client_disconnected")
    finally:
        if not exited:
            await process.signal(signal.SIGKILL)
        await _finish(reader, sandbox_id=sandbox_id)
        await _finish(outputs, sandbox_id=sandbox_id)


async def _finish(task: asyncio.Future[Any], *, sandbox_id: str) -> None:
    _ = task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except (SandboxError, ConnectionResetError) as exc:
        _LOG.warning("broker.stream.failed", sandboxId=sandbox_id, errorType=type(exc).__name__)


async def _connect(request: web.Request) -> web.WebSocketResponse:
    ws = await _open_ws(request)
    try:
        handle = _registry(request).get(request.match_info["sandbox_id"])
        stream = await handle.connect(_int_query(request, "port"))
    except SandboxError as exc:
        await _send_error(ws, exc)
        return ws
    await ws.send_str(wire.control(wire.OP_CONNECTED))
    await _serve_stream(ws, stream, sandbox_id=handle.id)
    return ws


async def _serve_stream(ws: web.WebSocketResponse, stream: ByteStream, *, sandbox_id: str) -> None:
    async def outbound() -> None:
        while chunk := await stream.read(_CHUNK):
            await ws.send_bytes(chunk)
        if not ws.closed:
            await ws.send_str(wire.control(wire.OP_EOF))

    async def inbound() -> None:
        async for message in ws:
            if message.type is WSMsgType.BINARY:
                await stream.write(cast("bytes", message.data))
            elif message.type is WSMsgType.TEXT and wire.parse_control(str(message.data))["op"] == wire.OP_EOF:
                await stream.write_eof()

    sender = asyncio.ensure_future(outbound())
    try:
        await inbound()
    except SandboxError as exc:
        _LOG.warning("broker.stream.inbound_failed", sandboxId=sandbox_id, errorType=type(exc).__name__)
    finally:
        await stream.aclose()
        await _finish(sender, sandbox_id=sandbox_id)


def create_broker_app(
    broker: SandboxBroker,
    verifier: BrokerAuthVerifier,
    *,
    region: str | None = None,
    metrics: Callable[[], str] | None = None,
) -> web.Application:
    """``region``: this broker's (OWD-12); a sandbox is created only for a caller whose spec names it. ``None``: no
    placement check (a broker that serves one region's callers only). ``metrics``: the process's registry as
    Prometheus text, served at ``METRICS_PATH`` to the caller every other route accepts (C9.04); ``None``: no route."""
    app = web.Application(middlewares=[_auth_middleware(verifier)], client_max_size=1024**2)
    app[_BROKER] = _Registry(broker)
    app[_REGION] = region
    _ = app.router.add_get(wire.LIVENESS, _liveness)
    _ = app.router.add_get(wire.HEALTH, _health)
    _ = app.router.add_get(wire.MODES, _modes)
    _ = app.router.add_post(wire.SANDBOXES, _acquire)
    _ = app.router.add_delete(wire.sandbox_path("{sandbox_id}"), _release)
    _ = app.router.add_post(wire.REAP, _reap)
    _ = app.router.add_post(wire.ERASE_SESSION, _erase_session)
    _ = app.router.add_post(wire.ERASE_ORG, _erase_org)
    _ = app.router.add_put(wire.sandbox_path("{sandbox_id}", wire.ARCHIVE), _put_archive)
    _ = app.router.add_get(wire.sandbox_path("{sandbox_id}", wire.ARCHIVE), _get_archive)
    _ = app.router.add_put(wire.sandbox_path("{sandbox_id}", wire.SEED), _seed_archive)
    _ = app.router.add_get(wire.sandbox_path("{sandbox_id}", wire.EXEC), _exec)
    _ = app.router.add_get(wire.sandbox_path("{sandbox_id}", wire.MEMORY), _memory)
    _ = app.router.add_post(wire.sandbox_path("{sandbox_id}", wire.FLOW_HOLD), _flow_hold)
    _ = app.router.add_get(wire.sandbox_path("{sandbox_id}", wire.CONNECT), _connect)
    if metrics is not None:
        text = metrics

        async def exposition(_: web.Request) -> web.Response:
            return web.Response(body=text().encode(), headers={"Content-Type": PROMETHEUS_TEXT})

        _ = app.router.add_get(METRICS_PATH, exposition)
    return app


__all__ = ["REGION_MISMATCH", "create_broker_app"]

"""The egress proxy over HTTP: the sandbox-facing data plane and the broker-facing control plane.

The data plane needs no credential: the connection's own addresses name the
sandbox (P5.4 §1.1). The control plane needs a credential the injected
verifier accepts, and refuses any request that arrives on an address a
sandbox can reach, whatever it carries.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from harness_core.http_limits import ReadGuard, ReadLimits, guarded, start_guarded_site
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress import wire
from harness_core.sandbox.egress.bindings import Revocation
from harness_core.sandbox.egress.control import refuse_wildcards
from harness_core.sandbox.egress.errors import (
    SECRET_BLOCKED,
    SECRET_REFUSALS,
    EgressRefused,
)
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.policy import (
    REFUSAL_HEADER,
    ResponseScrubber,
    parse_target,
)
from harness_core.sandbox.egress.postgres import PostgresFront
from harness_core.sandbox.egress.tunnel import DataPlaneFront, TunnelHandler, open_tcp
from harness_core.sandbox.egress.types import (
    EGRESS_CA_PATH,
    EGRESS_PROXY_HOST,
    ProxyRequest,
)
from harness_core.sandbox.egress.volume import MODEL_GATEWAY_ALIAS, alias_for
from harness_core.sandbox.errors import (
    BrokerUnauthorized,
    InvalidSandboxRequest,
    SandboxError,
)
from harness_core.sandbox.remote.wire import error_body, status_for
from harness_core.telemetry import security_signals
from harness_core.telemetry.exposition import METRICS_PATH, PROMETHEUS_TEXT

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from aiohttp.typedefs import Handler, Middleware

    from harness_core.sandbox.auth import BrokerAuthVerifier
    from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
    from harness_core.sandbox.egress.interception import EgressAuthority
    from harness_core.sandbox.egress.proxy import EgressProxy
    from harness_core.sandbox.egress.tunnel import Connector
    from harness_core.sandbox.egress.types import EgressBinding, Forward

UPSTREAM_CONNECT_TIMEOUT_S = 10.0
# Model streams can pause between events; a silent upstream is cut after this.
UPSTREAM_READ_TIMEOUT_S = 300.0
_CHUNK = 64 * 1024
_PROXY = web.AppKey("egress_proxy", object)
_INSTANCE = web.AppKey("egress_instance", str)
_LOG = HarnessLog.named("harness_core.sandbox.egress.server")


def _addresses(request: web.BaseRequest) -> tuple[str, str]:
    transport = request.transport
    sock = transport.get_extra_info("sockname") if transport is not None else None
    peer = transport.get_extra_info("peername") if transport is not None else None
    return (str(sock[0]) if sock else ""), (str(peer[0]) if peer else "")


# What the engine's model reads in the refusal body where the reason code alone would invite a retry: words only, no
# figure, host or id.
_PLAIN: dict[R, str] = {
    R.VOLUME_CAP_EXCEEDED: (
        "This run has moved more data than this deployment allows one run to move, so its network access is stopped "
        "for the rest of the run. Do not retry; tell the user the run reached its data limit."
    ),
    # SEC-A-01: the flow rule held the request; the person is asked on the run's card, so a blind retry loop is wasted.
    R.SITE_REQUEST_HELD: (
        "This conversation has read private data, so requests to this website wait for the user to allow it: a URL "
        "can carry what was read. PipesHub has asked them. Package registries still answer installs. Go on without "
        "this site for now, or tell the user it is waiting; once they allow it, the same request works."
    ),
    R.SITE_UPLOAD_HELD: (
        "This conversation has read private data, so uploads to this website wait for the user to allow them. "
        "PipesHub has asked them. Go on without uploading for now, or tell the user it is waiting; once they allow "
        "it, the same request works."
    ),
    R.SITE_RELAY: (
        "This website fetches other addresses or shows what it is sent to others, so every request to it counts as "
        "an upload and it is allowed read-only here. Do not retry; tell the user an admin must allow it read and write."
    ),
}
# WP-103: a request that carried what looked like a secret. The tag is the code an engine adapter reads from a refused
# call's message (``[gateway:<code>]``, as the gateways tag theirs), so the run ends for this reason, not ``http_403``.
_SECRET_PLAIN = (
    "PipesHub blocked this request because it looked like it contained a secret (an API key or access token). "
    f"Do not send it again with the same content; leave the secret out, or tell the user. [gateway:{SECRET_BLOCKED}]"
)


# WP103-FU-2: an engine's model call refused for its body reached the kernel as ``http_413``/``http_415`` and read as
# an engine error. Each carries the code that names what happened to the engine (``[gateway:<code>]``): a body over
# the limit is its context being too large to send, which the kernel compacts and retries (MG-10); an encoded body is
# PipesHub's own stop (``ModelCallEncoded``), as only an admin can change how the engine sends its calls.
_MODEL_CALL_PLAIN: dict[R, str] = {
    R.BODY_TOO_LARGE: (
        "This model call is larger than PipesHub lets one call be, so it was not sent. Make the conversation shorter "
        "(compact it, or leave out large pasted content, files or images) and try again. "
        "[gateway:context_length_exceeded]"
    ),
    R.BODY_ENCODED: (
        "PipesHub cannot check a compressed model call, so it was not sent. Do not retry; tell the user the coding "
        "engine sends its model calls in a way this server does not accept. [gateway:model_call_encoded]"
    ),
}


def refusal_response(refused: EgressRefused, *, model_call: bool = False) -> web.Response:
    """The refusal's body; ``model_call``: the request was an engine's call to the Model Gateway."""
    error: dict[str, str | None] = {"code": "egress_refused", "reason": refused.reason.value, "requestId": refused.request_id}
    plain = (
        _SECRET_PLAIN
        if refused.reason in SECRET_REFUSALS
        else (_MODEL_CALL_PLAIN.get(refused.reason) if model_call else None) or _PLAIN.get(refused.reason)
    )
    if plain is not None:
        error["message"] = plain
    return web.json_response({"error": error}, status=refused.status, headers={REFUSAL_HEADER: refused.reason.value})


async def _read_body(request: web.BaseRequest, limit: int) -> bytes:
    if request.method.upper() == "CONNECT":
        return b""
    declared = request.content_length
    if declared is not None and declared > limit:
        raise EgressRefused(R.BODY_TOO_LARGE, "request body exceeds the proxy's limit")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.content.iter_chunked(_CHUNK):
        size += len(chunk)
        if size > limit:
            raise EgressRefused(R.BODY_TOO_LARGE, "request body exceeds the proxy's limit")
        chunks.append(chunk)
    return b"".join(chunks)


class InFlight:
    """The data-plane requests each sandbox has in flight, cut when its binding goes (a release, a revocation): a
    streamed answer must not keep flowing to a sandbox whose egress has ended."""

    def __init__(self, bindings: InMemoryEgressBindings) -> None:
        self._tasks: dict[str, set[asyncio.Task[object]]] = {}
        bindings.on_unregister(self.cut)

    @contextlib.contextmanager
    def track(self, sandbox_id: str) -> Iterator[None]:
        task = asyncio.current_task()
        if task is None:
            yield
            return
        held = self._tasks.setdefault(sandbox_id, set())
        held.add(task)
        try:
            yield
        finally:
            held.discard(task)
            if not held and self._tasks.get(sandbox_id) is held:
                del self._tasks[sandbox_id]

    def cut(self, sandbox_id: str) -> None:
        for task in self._tasks.pop(sandbox_id, set()):
            _ = task.cancel()

    def count(self, sandbox_id: str) -> int:
        return len(self._tasks.get(sandbox_id, ()))


class EgressDataPlane:
    """``web.Server`` handler: authorize through ``EgressProxy``, forward, stream back scrubbed.

    ``intercepted``: the (host, port) of the ``CONNECT`` whose TLS the proxy ended, which every request on this
    connection is for. ``authority``: the certificate a bound sandbox may read at ``EGRESS_CA_PATH``."""

    def __init__(
        self,
        proxy: EgressProxy,
        session: aiohttp.ClientSession,
        *,
        intercepted: tuple[str, int] | None = None,
        authority: EgressAuthority | None = None,
        in_flight: InFlight | None = None,
    ) -> None:
        self._proxy = proxy
        self._session = session
        self._intercepted = intercepted
        self._authority = authority
        self._in_flight = in_flight or InFlight(proxy.bindings)

    async def __call__(self, request: web.BaseRequest) -> web.StreamResponse:
        local, peer = _addresses(request)
        if self._intercepted is None and _asks_for_authority(request):
            return self._authority_response(local, peer)
        request_id = self._proxy.new_request_id()
        headers = tuple(
            (k.decode("latin-1"), v.decode("utf-8", "surrogateescape")) for k, v in request.raw_headers
        )
        model_call = self._intercepted is None and is_model_call(request.method, request.raw_path, headers)
        try:
            body = await _read_body(request, self._proxy.limits.max_body_bytes)
        except EgressRefused as refused:
            self._proxy.record_refusal(refused, request_id=request_id, binding=self._known(local, peer), peer=peer)
            return refusal_response(refused, model_call=model_call)
        proxied = ProxyRequest(method=request.method, target=request.raw_path, headers=headers, body=body)
        try:
            if self._intercepted is None:
                forward = await self._proxy.authorize(proxied, local=local, peer=peer, request_id=request_id)
            else:
                host, port = self._intercepted
                forward = await self._proxy.authorize_intercepted(
                    proxied, host=host, port=port, local=local, peer=peer, request_id=request_id
                )
        except EgressRefused as refused:
            return refusal_response(refused, model_call=model_call)
        with self._in_flight.track(forward.sandbox_id):
            return await self._forward(request, forward, local, peer)

    def _authority_response(self, local: str, peer: str) -> web.StreamResponse:
        try:
            _ = self._proxy.binding_for(local=local, peer=peer)
        except EgressRefused as refused:
            self._proxy.record_refusal(refused, request_id=self._proxy.new_request_id(), peer=peer)
            return refusal_response(refused)
        if self._authority is None:
            return web.Response(status=404, text="this proxy does not end TLS\n")
        return web.Response(body=self._authority.certificate_pem, content_type="application/x-pem-file")

    def _volume_capped(self, forward: Forward, local: str, peer: str) -> EgressRefused:
        refused = EgressRefused(R.VOLUME_CAP_EXCEEDED, "the run moved more than the deployment's egress cap")
        self._proxy.record_refusal(refused, request_id=forward.request_id, binding=self._known(local, peer), peer=peer)
        return refused

    def _known(self, local: str, peer: str) -> EgressBinding | None:
        binding = self._proxy.bindings.for_local_address(local)
        return binding if binding is not None and binding.sandbox_address == peer else None

    async def _forward(self, request: web.BaseRequest, forward: Forward, local: str, peer: str) -> web.StreamResponse:
        # G-21: plain HTTP is sent to the deployment's proxy itself, so its credential rides the request; HTTPS opens a
        # CONNECT through it, and the credential rides only that (aiohttp's proxy_headers).
        secure = forward.url.startswith("https://")
        chained = forward.proxy is not None and not secure
        meter = forward.meter
        if meter is not None and not meter.sent(_request_bytes(request, forward.body)):
            return refusal_response(self._volume_capped(forward, local, peer))
        try:
            upstream = await self._session.request(
                forward.method,
                forward.url,
                headers=[*forward.headers, *(forward.proxy_headers if chained else ())],
                data=forward.body or None,
                allow_redirects=False,
                skip_auto_headers=("User-Agent", "Accept", "Accept-Encoding", "Content-Type"),
                server_hostname=forward.server_hostname,
                proxy=forward.proxy,
                proxy_headers=dict(forward.proxy_headers) if forward.proxy is not None and secure else None,
            )
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            refused = EgressRefused(R.UPSTREAM_UNAVAILABLE, f"upstream failed: {type(exc).__name__}")
            self._proxy.record_refusal(refused, request_id=forward.request_id, binding=self._known(local, peer), peer=peer)
            return refusal_response(refused)
        scrubbing = ResponseScrubber(forward.reflections)
        scrubber = scrubbing.body()
        response = web.StreamResponse(
            status=upstream.status,
            reason=scrubbing.reason(upstream.status),
            headers=list(scrubbing.headers(_pairs(upstream))),
        )
        try:
            _ = await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                if meter is not None and not meter.received(len(chunk)):
                    # Past the run's cap mid-answer: the status is sent, so the connection is cut instead.
                    _ = self._volume_capped(forward, local, peer)
                    if request.transport is not None:
                        request.transport.close()
                    return response
                out = scrubber.feed(chunk) if scrubber is not None else chunk
                if out:
                    await response.write(out)
            if scrubber is not None and (tail := scrubber.finish()):
                await response.write(tail)
            await response.write_eof()
            return response
        except (ConnectionResetError, aiohttp.ClientConnectionResetError):
            # The sandbox hung up mid-stream; nothing to answer.
            _LOG.info("egress.client_gone", sandboxId=forward.sandbox_id, proxyRequestId=forward.request_id)
            return response
        except TimeoutError:
            # aiohttp's sock_read cut (ServerTimeoutError is a TimeoutError): the status is sent, so the connection is
            # ended; uncaught, it surfaced as aiohttp's bare "Error handling request", which hid WP-127's fault.
            _LOG.warning(
                "egress.upstream_silent",
                sandboxId=forward.sandbox_id,
                proxyRequestId=forward.request_id,
                silenceS=UPSTREAM_READ_TIMEOUT_S,
            )
            if request.transport is not None:
                request.transport.close()
            return response
        finally:
            upstream.release()


def is_model_call(method: str, target: str, headers: tuple[tuple[str, str], ...]) -> bool:
    """Whether a plain-HTTP request is addressed to the Model Gateway, read from its target alone (before its body)."""
    try:
        parsed = parse_target(ProxyRequest(method=method, target=target, headers=headers, body=b""))
    except EgressRefused:
        return False
    return alias_for(parsed.host, "http") == MODEL_GATEWAY_ALIAS


def _request_bytes(request: web.BaseRequest, body: bytes) -> int:
    """What the sandbox sent for this request: its head as received and its body."""
    head = len(request.method) + len(request.raw_path) + 12
    return head + sum(len(k) + len(v) + 4 for k, v in request.raw_headers) + len(body)


def _asks_for_authority(request: web.BaseRequest) -> bool:
    """``GET`` of the certificate, from the proxy itself (origin-form, or absolute-form naming the proxy)."""
    if request.method.upper() != "GET":
        return False
    target = request.raw_path
    if target.startswith("/"):
        return target == EGRESS_CA_PATH
    parts = urlsplit(target)
    return parts.hostname == EGRESS_PROXY_HOST and parts.path == EGRESS_CA_PATH and not parts.query


class InterceptedHttpServer(web.Server):
    """The data plane's HTTP server for one intercepted connection; ``closed`` returns when that connection ends."""

    def __init__(
        self, handler: EgressDataPlane, *, guard: Callable[[asyncio.Protocol], asyncio.Protocol] | None = None
    ) -> None:
        super().__init__(handler, access_log=None, handler_cancellation=True)
        self._ended = asyncio.Event()
        self._guard = guard

    def __call__(self) -> asyncio.Protocol:  # pyright: ignore[reportIncompatibleMethodOverride]
        # The decrypted requests of an intercepted tunnel are a client's requests like any other: bounded too.
        handler = super().__call__()
        return self._guard(handler) if self._guard is not None else handler

    def connection_lost(self, handler: object, exc: BaseException | None = None) -> None:
        super().connection_lost(handler, exc)  # pyright: ignore[reportArgumentType]
        self._ended.set()

    async def closed(self) -> None:
        _ = await self._ended.wait()


def _pairs(response: aiohttp.ClientResponse) -> list[tuple[str, str]]:
    return [(k.decode("latin-1"), v.decode("utf-8", "surrogateescape")) for k, v in response.raw_headers]


def upstream_session() -> aiohttp.ClientSession:
    # No environment proxies, no decompression (bytes pass through as the gateway sent them).
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=UPSTREAM_CONNECT_TIMEOUT_S, sock_read=UPSTREAM_READ_TIMEOUT_S)
    return aiohttp.ClientSession(timeout=timeout, auto_decompress=False, trust_env=False)


def _error_response(error: SandboxError) -> web.Response:
    return web.json_response(error_body(error), status=status_for(error))


def _control_middleware(proxy: EgressProxy, verifier: BrokerAuthVerifier) -> Middleware:
    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
        local, peer = _addresses(request)
        if local in proxy.bindings.proxy_addresses():
            refused = EgressRefused(R.CONTROL_FROM_SANDBOX, "the control plane is not reachable from a sandbox")
            binding = proxy.bindings.for_local_address(local)
            proxy.record_refusal(refused, request_id=proxy.new_request_id(), binding=binding, peer=peer)
            return refusal_response(refused)
        if request.path != wire.LIVENESS and not await verifier.verify(dict(request.headers), path=request.path):
            security_signals.machine_credential_refused(request.headers)
            _LOG.warning("egress.control.unauthorized", path=request.path, method=request.method)
            return _error_response(BrokerUnauthorized("credential missing or not accepted"))
        try:
            return await handler(request)
        except SandboxError as exc:
            return _error_response(exc)
        except web.HTTPException:
            raise
        except Exception as exc:
            _LOG.error("egress.control.failed", exc=exc, path=request.path, errorType=type(exc).__name__)
            return _error_response(SandboxError("internal egress proxy error"))

    return middleware


def _proxy_of(request: web.Request) -> EgressProxy:
    return cast("EgressProxy", request.app[_PROXY])


async def _liveness(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _health(request: web.Request) -> web.Response:
    bindings = _proxy_of(request).bindings
    bound = await bindings.bound()
    return web.json_response(
        {
            "ok": True,
            "instance": request.app[_INSTANCE],
            "bindings": len(bound),
            "killSwitch": bindings.kill_switch,
            "egressStops": len(bindings.stops()),
        }
    )


async def _list(request: web.Request) -> web.Response:
    return web.json_response({"sandboxIds": sorted(await _proxy_of(request).bindings.bound())})


async def _put(request: web.Request) -> web.Response:
    try:
        binding = wire.BINDING.validate_python(await request.json())
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed binding: {type(exc).__name__}") from exc
    if binding.sandbox_id != request.match_info["sandbox_id"]:
        raise InvalidSandboxRequest("sandbox id in the path and the body differ")
    await _proxy_of(request).bindings.register(binding)
    _LOG.info(
        "egress.bound",
        sandboxId=binding.sandbox_id,
        routes=len(binding.policy.token_routes),
        destinations=len(binding.policy.destinations),
        **{k: v for k, v in binding.labels.items() if k not in ("event", "exc")},
    )
    return web.Response(status=204)


async def _revoke(request: web.Request) -> web.Response:
    try:
        raw: object = await request.json()
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed revocation: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise InvalidSandboxRequest("a revocation is a JSON object")
    scope = cast("dict[str, object]", raw)
    if set(scope) - {"orgId", "sessionId"} or any(not isinstance(v, str) or not v for v in scope.values()):
        raise InvalidSandboxRequest("a revocation names only a non-empty orgId and sessionId")
    org_id, session_id = cast("str | None", scope.get("orgId")), cast("str | None", scope.get("sessionId"))
    revoked = await _proxy_of(request).bindings.revoke(Revocation(org_id=org_id, session_id=session_id))
    _LOG.warning("egress.revoked", bindings=len(revoked), orgId=org_id or "*", sessionId=session_id or "*")
    return web.json_response({"sandboxIds": list(revoked)})


_MAX_CHARGE_BYTES = 1 << 40


async def _charge(request: web.Request) -> web.Response:
    """WP-63 part A: what the worker received for a run outside its sandboxes, counted against the run's cap."""
    try:
        raw: object = await request.json()
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed charge: {type(exc).__name__}") from exc
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    org_id, run_id, count = body.get("orgId"), body.get("runId"), body.get("bytes")
    if (
        set(body) != {"orgId", "runId", "bytes"}
        or not isinstance(org_id, str)
        or not org_id
        or not isinstance(run_id, str)
        or not run_id
        or type(count) is not int
        or not 0 <= count <= _MAX_CHARGE_BYTES
    ):
        raise InvalidSandboxRequest("a charge names a non-empty orgId and runId and a byte count")
    charged = _proxy_of(request).volume.charge(org_id=org_id, run_id=run_id, received=count)
    if count:
        _LOG.info("egress.volume_charged", bytes=count, allowed=charged.allowed)
    return web.json_response({"allowed": charged.allowed, "remaining": charged.remaining})


_GRANT_FIELDS = frozenset({"sandboxId", "placeholder", "host", "repository", "branch", "credentialRef", "ttlS"})


async def _push_grant(request: web.Request) -> web.Response:
    """GIT-FLOW: the one push a person allowed, from one bound sandbox to its own repository (``push_grants``)."""
    from harness_core.sandbox.egress.push_grants import PushGrantInvalid

    try:
        raw: object = await request.json()
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed push grant: {type(exc).__name__}") from exc
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    ttl = body.get("ttlS")
    strings = {k: body.get(k) for k in _GRANT_FIELDS - {"ttlS"}}
    if set(body) != _GRANT_FIELDS or type(ttl) is not int or not all(isinstance(v, str) and v for v in strings.values()):
        raise InvalidSandboxRequest("a push grant names its sandbox, placeholder, host, repository, branch, sealed credential and ttlS")
    proxy = _proxy_of(request)
    sandbox_id = cast("str", strings["sandboxId"])
    binding = proxy.bindings.get(sandbox_id)
    if binding is None:
        raise InvalidSandboxRequest("the sandbox is not bound")
    try:
        _ = proxy.push_grants.issue(
            binding,
            placeholder=cast("str", strings["placeholder"]),
            host=cast("str", strings["host"]),
            repository=cast("str", strings["repository"]),
            branch=cast("str", strings["branch"]),
            credential_ref=cast("str", strings["credentialRef"]),
            ttl_s=ttl,
        )
    except PushGrantInvalid as exc:
        raise InvalidSandboxRequest(str(exc)) from exc
    _LOG.info("egress.push_granted", sandboxId=sandbox_id, ttlS=ttl)
    return web.Response(status=204)


async def _site_grant(request: web.Request) -> web.Response:
    """The upload card: uploads the person allowed for one run, to one held website, from the sandbox serving it."""
    from harness_core.sandbox.egress.site_grants import SiteGrantInvalid

    try:
        raw: object = await request.json()
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed site grant: {type(exc).__name__}") from exc
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    if set(body) != {"sandboxId", "host", "runId"} or not all(isinstance(v, str) and v for v in body.values()):
        raise InvalidSandboxRequest("a site grant names its sandbox, host and run")
    proxy = _proxy_of(request)
    sandbox_id = cast("str", body["sandboxId"])
    binding = proxy.bindings.get(sandbox_id)
    if binding is None:
        raise InvalidSandboxRequest("the sandbox is not bound")
    try:
        proxy.site_grants.issue(
            binding, host=cast("str", body["host"]), run_id=cast("str", body["runId"]), run_label=proxy.run_label
        )
    except SiteGrantInvalid as exc:
        raise InvalidSandboxRequest(str(exc)) from exc
    _LOG.info("egress.site_released", sandboxId=sandbox_id)
    return web.Response(status=204)


async def _flow_hold(request: web.Request) -> web.Response:
    """SEC-A-02: the run's conversation now holds private data (or its level forbids web egress), mid-turn."""
    from harness_core.sandbox.egress.site_grants import SiteGrantInvalid

    try:
        raw: object = await request.json()
    except ValueError as exc:
        raise InvalidSandboxRequest(f"malformed flow hold: {type(exc).__name__}") from exc
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    if set(body) != {"sandboxId", "runId", "hold"} or not all(isinstance(v, str) and v for v in body.values()):
        raise InvalidSandboxRequest("a flow hold names its sandbox, run and hold")
    proxy = _proxy_of(request)
    sandbox_id = cast("str", body["sandboxId"])
    try:
        narrowed = await proxy.hold_flow(sandbox_id, run_id=cast("str", body["runId"]), hold=cast("str", body["hold"]))
    except SiteGrantInvalid as exc:
        raise InvalidSandboxRequest(str(exc)) from exc
    _LOG.info("egress.flow_held", sandboxId=sandbox_id, hold=body["hold"], narrowed=narrowed)
    return web.Response(status=204)


async def _delete(request: web.Request) -> web.Response:
    sandbox_id = request.match_info["sandbox_id"]
    proxy = _proxy_of(request)
    if sandbox_id in await proxy.bindings.bound():
        await proxy.bindings.unregister(sandbox_id)
        _LOG.info("egress.unbound", sandboxId=sandbox_id)
    return web.Response(status=204)


def _metrics_route(metrics: Callable[[], str]) -> Handler:
    async def serve(_: web.Request) -> web.Response:
        return web.Response(body=metrics().encode("utf-8"), headers={"Content-Type": PROMETHEUS_TEXT})

    return serve


def create_control_app(
    proxy: EgressProxy,
    verifier: BrokerAuthVerifier,
    *,
    instance: str,
    metrics: Callable[[], str] | None = None,
) -> web.Application:
    """``metrics``: this process's metrics at ``METRICS_PATH`` (C9.04), under the credential every control route takes
    and refused from a sandbox's address like them; unset, the route is not served."""
    app = web.Application(middlewares=[_control_middleware(proxy, verifier)])
    app[_PROXY] = proxy
    app[_INSTANCE] = instance
    _ = app.router.add_get(wire.LIVENESS, _liveness)
    if metrics is not None:
        _ = app.router.add_get(METRICS_PATH, _metrics_route(metrics))
    _ = app.router.add_get(wire.HEALTH, _health)
    _ = app.router.add_get(wire.BINDINGS, _list)
    _ = app.router.add_put(wire.BINDINGS + "/{sandbox_id}", _put)
    _ = app.router.add_delete(wire.BINDINGS + "/{sandbox_id}", _delete)
    _ = app.router.add_post(wire.REVOCATIONS, _revoke)
    _ = app.router.add_post(wire.VOLUME_CHARGES, _charge)
    _ = app.router.add_post(wire.PUSH_GRANTS, _push_grant)
    _ = app.router.add_post(wire.SITE_GRANTS, _site_grant)
    _ = app.router.add_post(wire.FLOW_HOLDS, _flow_hold)
    return app


class EgressServer:
    """Both listeners and the upstream session; ``start`` then ``stop``."""

    def __init__(
        self,
        proxy: EgressProxy,
        verifier: BrokerAuthVerifier,
        *,
        instance: str,
        connector: Connector = open_tcp,
        authority: EgressAuthority | None = None,
        read_limits: ReadLimits | None = None,
        metrics: Callable[[], str] | None = None,
    ) -> None:
        self.proxy = proxy
        self._metrics = metrics
        self._verifier = verifier
        self.instance = instance
        self._read_limits = read_limits or ReadLimits.from_env()
        self._connector = connector
        self.authority = authority
        self.database_port: int | None = None
        self._runners: list[web.BaseRunner] = []
        self._front: tuple[asyncio.Server, web.Server, TunnelHandler] | None = None
        self._database: tuple[asyncio.Server, PostgresFront] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._flusher: asyncio.Task[None] | None = None
        self.in_flight = InFlight(proxy.bindings)

    async def start(
        self,
        *,
        host: str,
        data_port: int,
        control_port: int,
        control_host: str | Sequence[str] | None = None,
        database_port: int | None = None,
    ) -> tuple[int, int]:
        """Both listeners. The control plane binds only ``control_host`` (``host`` when not given), never a wildcard:
        a sandbox's network reaches the proxy, so a wildcard control listener would reach every sandbox (E2E4).
        ``database_port``: also serve sandboxes' PostgreSQL routes there (``POSTGRES_PORT``; 0 picks one)."""
        named = (control_host,) if isinstance(control_host, str) else control_host
        control_hosts = refuse_wildcards(named if named is not None else (host,))
        # C6.12: a restarted proxy keeps each run's count (its cap) and each org's baseline before it serves.
        _ = await self.proxy.volume.load()
        self._flusher = asyncio.ensure_future(self._flush_volume())
        self._session = upstream_session()
        # No access log: request lines carry URLs, and a URL may carry a credential (SEC-13).
        # A sandbox that hangs up (an engine interrupting its model call) cancels the upstream call too.
        plane = EgressDataPlane(self.proxy, self._session, authority=self.authority, in_flight=self.in_flight)
        http = web.Server(plane, access_log=None, handler_cancellation=True)
        ports: list[int] = []
        if self.proxy.limits.connect_tunnels or self.proxy.limits.intercepts:
            ports.append(await self._start_front(http, host, data_port))
        else:
            ports.append(await self._start_runner(web.ServerRunner(http), host, data_port, listener="egress_data"))
        control = web.AppRunner(create_control_app(self.proxy, self._verifier, instance=self.instance, metrics=self._metrics), access_log=None)
        ports.append(await self._start_runner(control, control_hosts[0], control_port, listener="egress_control"))
        for extra in control_hosts[1:]:
            _ = await start_guarded_site(control, extra, ports[1], limits=self._read_limits, listener="egress_control")
        if database_port is not None:
            front = PostgresFront(self.proxy, connector=self._connector)
            listener = await asyncio.start_server(front.start, host, database_port)
            self._database = (listener, front)
            self.database_port = cast("tuple[str, int]", listener.sockets[0].getsockname())[1]
        _LOG.info(
            "egress.started",
            dataPort=ports[0],
            controlPort=ports[1],
            controlHosts=list(control_hosts),
            instance=self.instance,
            connectTunnels=self.proxy.limits.connect_tunnels,
            intercepts=self.proxy.limits.intercepts and self.authority is not None,
            upstreamProxy=upstream.url if (upstream := self.proxy.upstream_egress.upstream) is not None else None,
            privateAllow=[str(entry) for entry in self.proxy.upstream_egress.private_allow],
        )
        return ports[0], ports[1]

    async def _flush_volume(self) -> None:
        volume = self.proxy.volume
        while True:
            await asyncio.sleep(volume.limits.flush_s)
            try:
                _ = await volume.flush()
            except Exception as exc:
                _LOG.warning("egress.volume_flush_failed", errorType=type(exc).__name__)

    async def _start_runner(self, runner: web.BaseRunner, host: str, port: int, *, listener: str) -> int:
        await runner.setup()
        self._runners.append(runner)
        return await start_guarded_site(runner, host, port, limits=self._read_limits, listener=listener)

    async def _start_front(self, http: web.Server, host: str, port: int) -> int:
        session = self._session
        authority = self.authority if self.proxy.limits.intercepts else None
        assert session is not None

        limits = self._read_limits

        def intercepted(target_host: str, target_port: int) -> InterceptedHttpServer:
            return InterceptedHttpServer(
                EgressDataPlane(self.proxy, session, intercepted=(target_host, target_port), in_flight=self.in_flight),
                guard=lambda inner: ReadGuard(inner, limits, listener="egress_intercepted"),
            )

        tunnels = TunnelHandler(
            self.proxy,
            connector=self._connector,
            interceptor=intercepted if authority is not None else None,
            server_context=authority.server_context if authority is not None else None,
        )
        front = await asyncio.get_running_loop().create_server(
            guarded(lambda: DataPlaneFront(http, tunnels), self._read_limits, listener="egress_data"), host, port
        )
        self._front = (front, http, tunnels)
        return cast("tuple[str, int]", front.sockets[0].getsockname())[1]

    async def stop(self) -> None:
        if self._database is not None:
            listener, front = self._database
            listener.close()
            await front.aclose()
            self._database = None
        if self._front is not None:
            front, http, tunnels = self._front
            front.close()
            await tunnels.aclose()
            with contextlib.suppress(Exception):
                await http.shutdown(5.0)
            self._front = None
        for runner in reversed(self._runners):
            with contextlib.suppress(Exception):
                await runner.cleanup()
        self._runners.clear()
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._flusher is not None:
            _ = self._flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flusher
            self._flusher = None
            try:
                _ = await self.proxy.volume.flush()
            except Exception as exc:
                _LOG.warning("egress.volume_flush_failed", errorType=type(exc).__name__)
        _LOG.info("egress.stopped", instance=self.instance)


__all__ = [
    "EgressDataPlane",
    "is_model_call",
    "EgressServer",
    "InFlight",
    "InterceptedHttpServer",
    "create_control_app",
    "refusal_response",
    "upstream_session",
]

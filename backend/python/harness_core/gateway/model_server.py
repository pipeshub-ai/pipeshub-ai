"""The model gateway over HTTP (aiohttp): ``/v1/chat/completions``, ``/v1/responses``, ``/v1/messages``,
``/v1/messages/count_tokens``, Gemini's ``/v1beta/models/{model}:{generateContent,streamGenerateContent,countTokens}``,
``/v1/models``, ``/healthz``, and ``/metrics`` for the worker credential (never a run's token) when built with an
exposition.

The listener sits behind the egress proxy (P5.4), which forwards a sandbox's
``http://model-gateway:8097/v1/...`` here with the run's gateway token. Any
caller without a valid token is refused before anything is read but its
headers, so a pod sharing the proxy's netns (K8s) gains nothing by calling
it directly. Each route answers errors in its own wire's vocabulary.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web

from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.errors import (
    BodyTooLarge,
    ResponseStateUnsupported,
    RouteNotAllowed,
)
from harness_core.gateway.gemini_wire import GEMINI_WIRE, envelope, model_method
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.model_gateway import (
    ChatCompletion,
    ChatStream,
    ProviderCallFailed,
    refusal_status,
)
from harness_core.gateway.responses_wire import RESPONSES_WIRE
from harness_core.http_limits import ReadLimits, start_guarded_site
from harness_core.logs import HarnessLog
from harness_core.refusals import HarnessRefusal
from harness_core.telemetry.exposition import METRICS_PATH, PROMETHEUS_TEXT
from harness_core.telemetry.metrics import (
    NOOP_GATEWAY_METRICS,
    UNMATCHED,
    GatewayRequest,
    outcome_of,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from harness_core.gateway.model_gateway import ModelGateway
    from harness_core.gateway.wire import GatewayWire, Json
    from harness_core.telemetry.exposition import MetricsExposition
    from harness_core.telemetry.metrics import GatewayMetrics

MODEL_GATEWAY_PORT = 8096
REFUSAL_HEADER = "X-Gateway-Refusal"
# Engines send whole conversations; the egress proxy caps bodies at 32 MiB (P5.4) and so does this.
MAX_BODY_BYTES = 32 * 1024 * 1024
# ADR-GW-01 D1: a stream with nothing to send this long gets its wire's keepalive. A model composing a large tool
# call or thinking unseen sends nothing for minutes, and the egress proxy cuts an upstream silent for
# UPSTREAM_READ_TIMEOUT_S (300 s); the write also tells the gateway its caller is gone.
HEARTBEAT_S = 15.0
_GATEWAY = web.AppKey("model_gateway", object)
_METRICS = web.AppKey("model_gateway_metrics", object)
_EXPOSITION = web.AppKey("model_gateway_exposition", object)
_HEARTBEAT = web.AppKey("model_gateway_heartbeat_s", float)
# Each route's metric name (harness_core.telemetry.metrics.MODEL_ROUTES), by the path it is added under.
_ROUTE_NAMES = {
    "/healthz": "healthz",
    METRICS_PATH: "metrics",
    "/v1/models": "models",
    "/v1/chat/completions": "chat_completions",
    "/v1/responses": "responses",
    "/v1/responses/{responseId}": "responses_stored",
    "/v1/responses/{responseId}/{action}": "responses_stored",
    "/v1/messages": "messages",
    "/v1/messages/count_tokens": "count_tokens",
    "/v1beta/models/{target}": "gemini",
}
_LOG = HarnessLog.named("harness_core.gateway.model_server")


def _bearer(request: web.Request) -> str | None:
    value = request.headers.get("Authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


def _headers(request: web.Request) -> dict[str, str]:
    return {name.lower(): value for name, value in request.headers.items() if name.lower() != "authorization"}


def _gateway(request: web.Request) -> ModelGateway:
    return request.app[_GATEWAY]  # pyright: ignore[reportReturnType]


def _refusal(refused: HarnessRefusal, wire: GatewayWire[Any] = CHAT_WIRE) -> web.Response:
    code, status = refused.reason_code, refusal_status(refused)
    body, extra = wire.error(status, code, str(refused) or code, param=getattr(refused, "param", None))
    headers = {**extra, REFUSAL_HEADER: code}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="model-gateway"'
    return web.json_response(body, status=status, headers=headers)


def _failure(failed: ProviderCallFailed, wire: GatewayWire[Any] = CHAT_WIRE) -> web.Response:
    body, headers = wire.error(failed.status, failed.code, str(failed), provider=True)
    return web.json_response(body, status=failed.status, headers=headers)


async def _models(request: web.Request) -> web.Response:
    try:
        return web.json_response(await _gateway(request).models(_bearer(request), _headers(request)))
    except HarnessRefusal as refused:
        return _refusal(refused)


async def _read_json(request: web.Request) -> object:
    try:
        raw = await request.read()
    except web.HTTPRequestEntityTooLarge:
        # Read only after the token verified, so the refusal is the run's (logged and reported by the gateway).
        raise BodyTooLarge(f"the request body is over {MAX_BODY_BYTES} bytes") from None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return None


async def _serve(request: web.Request, wire: GatewayWire[Any]) -> web.StreamResponse:
    try:
        outcome = await _gateway(request).serve(wire, _bearer(request), _headers(request), lambda: _read_json(request))
    except HarnessRefusal as refused:
        return _refusal(refused, wire)
    except ProviderCallFailed as failed:
        return _failure(failed, wire)
    return await _answer(request, outcome)


async def _chat(request: web.Request) -> web.StreamResponse:
    return await _serve(request, CHAT_WIRE)


async def _responses(request: web.Request) -> web.StreamResponse:
    return await _serve(request, RESPONSES_WIRE)


async def _messages(request: web.Request) -> web.StreamResponse:
    return await _serve(request, MESSAGES_WIRE)


async def _count_tokens(request: web.Request) -> web.Response:
    try:
        tokens = await _gateway(request).count_tokens(
            MESSAGES_WIRE, _bearer(request), _headers(request), lambda: _read_json(request)
        )
    except HarnessRefusal as refused:
        return _refusal(refused, MESSAGES_WIRE)
    return web.json_response({"input_tokens": tokens})


async def _gemini(request: web.Request) -> web.StreamResponse:
    """Gemini's ``{model}:{method}``: the model and the method are the path's, so the body is read into the
    wire's envelope. A streamed answer is SSE whatever ``alt`` says (Gemini CLI always asks ``alt=sse``)."""
    parsed = model_method(request.match_info["target"])
    if parsed is None:
        raise web.HTTPNotFound
    model, method = parsed
    stream = method == "streamGenerateContent"

    async def read() -> object:
        return envelope(model, await _read_json(request), stream=stream)

    if method == "countTokens":
        try:
            tokens = await _gateway(request).count_tokens(GEMINI_WIRE, _bearer(request), _headers(request), read)
        except HarnessRefusal as refused:
            return _refusal(refused, GEMINI_WIRE)
        return web.json_response({"totalTokens": tokens})
    try:
        outcome = await _gateway(request).serve(GEMINI_WIRE, _bearer(request), _headers(request), read)
    except HarnessRefusal as refused:
        return _refusal(refused, GEMINI_WIRE)
    except ProviderCallFailed as failed:
        return _failure(failed, GEMINI_WIRE)
    return await _answer(request, outcome)


async def _stored_response(request: web.Request) -> web.Response:
    """Retrieval, deletion, cancellation, input items: every one needs a stored response."""
    try:
        await _gateway(request).refuse_stateful(
            _bearer(request), _headers(request), ResponseStateUnsupported("this gateway keeps no responses")
        )
    except HarnessRefusal as refused:
        return _refusal(refused, RESPONSES_WIRE)
    raise AssertionError("refuse_stateful always raises")


async def _answer(request: web.Request, outcome: ChatCompletion | ChatStream) -> web.StreamResponse:
    if not isinstance(outcome, ChatStream):
        return web.json_response(outcome.body)
    return await _sse(request, outcome, heartbeat_s=request.app[_HEARTBEAT])  # pyright: ignore[reportArgumentType]


async def _next(chunks: AsyncIterator[Json]) -> Json:
    return await anext(chunks)


async def _sse(request: web.Request, stream: ChatStream, heartbeat_s: float = HEARTBEAT_S) -> web.StreamResponse:
    response = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
    chunks = aiter(stream.chunks)
    pending: asyncio.Task[Json] | None = None
    try:
        _ = await response.prepare(request)
        while True:
            waiting = pending if pending is not None else asyncio.create_task(_next(chunks))
            pending = waiting
            done, _ = await asyncio.wait({waiting}, timeout=heartbeat_s)
            if not done:
                # The next chunk keeps coming in its own task; only the silence is filled.
                if stream.keepalive is not None:
                    await response.write(stream.keepalive)
                continue
            pending = None
            try:
                chunk = waiting.result()
            except StopAsyncIteration:
                break
            await response.write(stream.frame(chunk))
        if stream.terminal:
            await response.write(stream.terminal)
        await response.write_eof()
    except (ConnectionResetError, ConnectionError):
        _LOG.info("model_gateway.client_gone")
    finally:
        if pending is not None and not pending.done():
            # The caller left (or this handler was cancelled for it): the provider's stream ends with it.
            _ = pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        await stream.aclose()
    return response


async def _health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _exposition(request: web.Request) -> web.Response:
    """The process's metrics, for the worker credential only: a sandbox reaching this listener through the egress
    proxy carries a run's gateway token, which is refused like no credential at all."""
    exposition: MetricsExposition | None = request.app[_EXPOSITION]  # pyright: ignore[reportAssignmentType]
    if exposition is None:
        raise web.HTTPNotFound
    text = await exposition.answer(request.headers)
    if text is None:
        return web.json_response(
            {"error": {"code": "credential_not_accepted", "message": "the worker credential is required"}},
            status=401,
            headers={REFUSAL_HEADER: "credential_not_accepted"},
        )
    return web.Response(body=text.encode("utf-8"), headers={"Content-Type": PROMETHEUS_TEXT})


def _wire_of(path: str) -> GatewayWire[Any]:
    if path.startswith("/v1beta/"):
        return GEMINI_WIRE
    if path.startswith("/v1/messages"):
        return MESSAGES_WIRE
    if path.startswith("/v1/responses"):
        return RESPONSES_WIRE
    return CHAT_WIRE


@web.middleware
async def _routes_only(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    """A path or method nothing serves answers in the wire's own error shape, before any token is read; the log
    names the status, never the path (a caller may put anything in it)."""
    try:
        return await handler(request)
    except (web.HTTPNotFound, web.HTTPMethodNotAllowed) as unserved:
        refused = RouteNotAllowed("this gateway serves no such route", status=unserved.status)
        _LOG.warning("model_gateway.refused", reason=refused.reason_code, status=unserved.status)
        return _refusal(refused, _wire_of(request.path))


@web.middleware
async def _measured(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    """One observation per request, by route name and outcome, until its last byte (a stream's end). Outermost, so
    it sees the answer ``_routes_only`` gives an unserved path."""
    started = time.monotonic()
    status, refusal = 500, None
    try:
        response = await handler(request)
        status, refusal = response.status, response.headers.get(REFUSAL_HEADER)
        return response
    except web.HTTPException as raised:
        status = raised.status
        raise
    finally:
        resource = request.match_info.route.resource
        metrics: GatewayMetrics = request.app[_METRICS]  # pyright: ignore[reportAssignmentType]
        metrics.request(
            GatewayRequest(
                gateway="model",
                route=_ROUTE_NAMES.get(resource.canonical, UNMATCHED) if resource is not None else UNMATCHED,
                outcome=outcome_of(status, refused=refusal is not None),
                seconds=time.monotonic() - started,
                reason=refusal,
            )
        )


def create_model_gateway_app(
    gateway: ModelGateway,
    metrics: GatewayMetrics = NOOP_GATEWAY_METRICS,
    *,
    exposition: MetricsExposition | None = None,
    heartbeat_s: float = HEARTBEAT_S,
) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY_BYTES, middlewares=[_measured, _routes_only])
    app[_GATEWAY] = gateway
    app[_HEARTBEAT] = heartbeat_s
    app[_METRICS] = metrics
    app[_EXPOSITION] = exposition
    _ = app.router.add_get("/healthz", _health)
    _ = app.router.add_get(METRICS_PATH, _exposition)
    _ = app.router.add_get("/v1/models", _models)
    _ = app.router.add_post("/v1/chat/completions", _chat)
    _ = app.router.add_post("/v1/responses", _responses)
    _ = app.router.add_route("*", "/v1/responses/{responseId}", _stored_response)
    _ = app.router.add_route("*", "/v1/responses/{responseId}/{action}", _stored_response)
    _ = app.router.add_post("/v1/messages", _messages)
    _ = app.router.add_post("/v1/messages/count_tokens", _count_tokens)
    _ = app.router.add_post("/v1beta/models/{target}", _gemini)
    return app


class ModelGatewayServer:
    def __init__(
        self,
        gateway: ModelGateway,
        *,
        read_limits: ReadLimits | None = None,
        metrics: GatewayMetrics = NOOP_GATEWAY_METRICS,
        exposition: MetricsExposition | None = None,
        heartbeat_s: float = HEARTBEAT_S,
    ) -> None:
        # ADR-GW-01 D2: a call whose caller hung up is cancelled, so the provider's request closes with it rather
        # than running (and billing) to its end; aiohttp's default lets the handler run on.
        self._runner = web.AppRunner(
            create_model_gateway_app(gateway, metrics, exposition=exposition, heartbeat_s=heartbeat_s),
            access_log=None,
            handler_cancellation=True,
        )
        self._read_limits = read_limits or ReadLimits.from_env()

    async def start(self, *, host: str, port: int = MODEL_GATEWAY_PORT) -> int:
        await self._runner.setup()
        return await start_guarded_site(self._runner, host, port, limits=self._read_limits, listener="model_gateway")

    async def stop(self) -> None:
        await self._runner.cleanup()


__all__ = [
    "MAX_BODY_BYTES",
    "MODEL_GATEWAY_PORT",
    "REFUSAL_HEADER",
    "ModelGatewayServer",
    "create_model_gateway_app",
]

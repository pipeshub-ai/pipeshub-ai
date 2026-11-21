"""The real lane for engines (E0): a pinned engine image under the Docker broker, behind the real
egress proxy, against real HTTP gateways.

Topology (runc, local Docker):

* ``pxh-e0-up-<sfx>``: an ordinary bridge holding the proxy container. The test
  process serves on that bridge's gateway address (reachable from the proxy, not
  from sandboxes, whose only network is their own /29 with the proxy):
  - a scripted OpenAI-compatible model (``ConformanceGateway``'s brain over HTTP,
    streaming chat completions) at ``/m/<key>/v1``, one key per scripted gateway;
  - a fake of Node's gateway-token route, minting tokens exactly as Node does
    (``TokenMint``) for the run attempt a sandbox's labels name;
  - E2's ``ToolGatewayApp`` (uvicorn) at ``/harness/tool-gateway``, verifying
    those tokens.
* the proxy container runs P5.4's proxy with ``NodeGatewayTokenSource``
  (``proxy_entry.py``); each sandbox's egress allowlist names its own upstreams
  (``EgressDestination.upstream``) for both gateways.
* the broker is ``DockerSandboxBroker`` in process with P5.4's Docker wiring.

Everything the lane creates carries ``pipeshub.harness.owner=x-lead`` (or ``PXH_HARNESS_OWNER``) and is
removed by name or label at teardown. The stack's network, proxy and Redis also
carry ``docker_session``'s labels (suite ``STACK_SUITE``), so a lane killed before
teardown leaves them to the session finalizer and the next stack's stale reaper.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import os
import re
import socket
import sysconfig
import time
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from aiohttp import web
from fastapi import FastAPI

from app.harness.gateway.mcp import ToolGatewayApp
from app.harness.gateway.org_binding import OrgBoundGatewayTokenVerifier
from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from app.harness.sandbox.docker_egress import DockerEgressWiring
from harness_core.access.memory import (
    InMemoryRunModelGrants,
    StaticModelAccessResolver,
)
from harness_core.access.types import RunBudget, RunModelGrant, neutral_models
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.tree import usd_micros
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from harness_core.clock import SystemClock
from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_core.gateway.model_gateway import ModelGateway
from harness_core.gateway.model_server import HEARTBEAT_S, create_model_gateway_app
from harness_core.ids import RandomIds
from harness_core.models.transports.openai import OpenAITransport
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT
from harness_core.sandbox.egress.volume import GATEWAY_ALIASES
from harness_core.sandbox.pool import WarmPoolConfig
from harness_core.sandbox.types import RuntimeMode
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.script import CONTEXT_OVERFLOW, ModelReply
from harness_core.testing.gateway import TokenMint
from harness_core.testing.model_gateway import org_key_route
from harness_core.testing.sandbox.egress import EgressRequest
from tests.support import docker_session
from tests.support.docker_networks import (
    EGRESS_CONTROL_ALIAS,
    control_alias_config,
    create_rig_network,
)
from tests.unit.harness.engines.lane_releases import (
    lane_admits_candidates,
    lane_release,
)
from tests.unit.harness.gateway.binding_keys import TEST_ATTESTER, proof_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.access.types import PinnedRoute
    from harness_core.engines.binary import PinnedBinary
    from harness_core.gateway.types import GatewayBinding
    from harness_core.sandbox.types import SandboxSpec
    from harness_core.testing.engines.gateway import ConformanceGateway

PYTHON_ROOT = Path(__file__).resolve().parents[4]
ALLOWLIST = json.loads((PYTHON_ROOT / "harness_engines" / "allowlist.json").read_text())
OPENCODE = next(e for e in ALLOWLIST["engines"] if e["kind"] == "opencode")
IMAGE: str = lane_release("opencode").image
HELPER_IMAGE = "python:3.12-slim"
# A work package running the lanes on a shared host labels what it creates as its own (``PXH_HARNESS_OWNER``).
OWNER = {"pipeshub.harness.owner": os.environ.get("PXH_HARNESS_OWNER", "x-lead"), "pipeshub.agent": "E0"}
STACK_SUITE = "real-stack"
EGRESS_POOL = "10.213.0.0/16"
REDIS_IMAGE = "redis:7.4-bookworm"
GATEWAY_MOUNT = "/harness/tool-gateway"
TOKEN_ROUTE = "/api/v1/agent-harness/internal/runs/{run_id}/gateway-token"
# OpenAI's answer to a prompt past the model's window (MG-10): the provider error the model gateway classifies.
OVERFLOW_ERROR = {
    "error": {
        "message": "This model's maximum context length is 128000 tokens. However, your messages resulted in 260000 "
        "tokens. Please reduce the length of the messages.",
        "type": "invalid_request_error",
        "param": "messages",
        "code": "context_length_exceeded",
    }
}
# The model route the proxy's static upstream table names (the deployment's model gateway, E3 in production).
STATIC_MODEL_KEY = "deployment"
WORKER_CREDENTIAL = "e0-worker-credential"
# The org's provider key the model gateway holds (vendor-shaped: credential scans must find it by shape).
PROVIDER_KEY = "sk-proj-" + "E8ScriptedProviderOrgKey0123456789"
_PROXY = (
    "import sys, runpy; sys.path.insert(0, '/src'); sys.path.append('/site'); "
    "runpy.run_module('tests.unit.harness.engines.proxy_entry', run_name='__main__')"
)


def engine_image(kind: str) -> str:
    """The run image the allowlist names for an engine kind, or a candidate's under test (CC1)."""
    return lane_release(kind).image


def lane_available(image: str = IMAGE) -> tuple[bool, str]:
    try:
        import docker

        client = docker.from_env()
        try:
            client.ping()
            client.images.get(image)
            client.images.get(HELPER_IMAGE)
        finally:
            client.close()
    except Exception as exc:
        return False, f"needs Docker, {image} and {HELPER_IMAGE}: {type(exc).__name__}"
    return True, ""


def _free_port(host: str) -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _text(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    return "".join(as_str(as_object(part).get("text")) for part in as_list(content))


def _image_types(body: Mapping[str, JsonValue]) -> list[str]:
    """The media type of every ``image_url`` part of a chat-completions body's messages."""
    found: list[str] = []
    for message in (as_object(m) for m in as_list(body.get("messages"))):
        for part in (as_object(p) for p in as_list(message.get("content"))):
            url = as_str(as_object(part.get("image_url")).get("url"))
            if part.get("type") == "image_url" and url.startswith("data:"):
                found.append(url[5:].split(";", 1)[0])
    return found


def to_conformance(body: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """An OpenAI chat-completions body as the conformance gateway's model reads it."""
    messages: list[JsonValue] = []
    for raw in as_list(body.get("messages")):
        message = as_object(raw)
        calls = [
            {"id": as_str(c.get("id")), "name": as_str(f.get("name")), "arguments": as_str(f.get("arguments"))}
            for c in (as_object(item) for item in as_list(message.get("tool_calls")))
            for f in (as_object(c.get("function")),)
        ]
        out: dict[str, JsonValue] = {"role": as_str(message.get("role")), "content": _text(message.get("content"))}
        if out["role"] == "developer":
            out["role"] = "system"
        if calls:
            out["tool_calls"] = calls  # type: ignore[assignment]
        if isinstance(message.get("tool_call_id"), str):
            out["tool_call_id"] = message["tool_call_id"]
        messages.append(out)
    # The schema travels too, so the scripted model names a tool's arguments as the tool does (WP-105).
    tools: list[JsonValue] = [
        {
            "name": as_str(function.get("name")),
            "description": as_str(function.get("description")),
            "parameters": function.get("parameters"),
        }
        for function in (as_object(as_object(t).get("function")) for t in as_list(body.get("tools")))
    ]
    return {"messages": messages, "tools": tools}


def _chunks(reply: ModelReply) -> list[dict[str, Any]]:
    base: dict[str, Any] = {"id": "chatcmpl-e0", "object": "chat.completion.chunk", "created": 0, "model": "engine-model"}
    if reply.tool_calls:
        delta: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": [
                {"index": i, "id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for i, c in enumerate(reply.tool_calls)
            ],
        }
        finish = "tool_calls"
    else:
        delta, finish = {"role": "assistant", "content": reply.content}, "stop"
    usage = {
        "prompt_tokens": reply.usage.input_tokens,
        "completion_tokens": reply.usage.output_tokens,
        "total_tokens": reply.usage.input_tokens + reply.usage.output_tokens,
    }
    return [
        {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        {**base, "choices": [], "usage": usage},
    ]


@dataclass
class ModelAndNode:
    """The scripted model gateway and Node's token route, on one aiohttp server."""

    mint: TokenMint
    gateways: dict[str, ConformanceGateway] = field(default_factory=dict[str, "ConformanceGateway"])
    authorizations: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    bindings: dict[tuple[str, str], GatewayBinding] = field(default_factory=dict[tuple[str, str], "GatewayBinding"])
    issued: list[str] = field(default_factory=list[str])
    budget_spent: set[str] = field(default_factory=set[str])
    paths: list[str] = field(default_factory=list[str])
    # Per model route, the digest of each request's whole ``tools`` array (names, descriptions, schemas, order): the
    # prefix a provider caches opens with it (recs audit R4).
    tool_lists: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    # Per model route, seconds the stream falls silent after its first chunk (WP127-FU-1: a model thinking mid-answer).
    pauses: dict[str, float] = field(default_factory=dict[str, float])
    # Per model route, the media type of each image the provider was sent (FIXPDFSKILL-FU-1): the scripted model reads
    # only text, so this is where an image an engine looked at is seen to reach the provider.
    images: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def register(self, gateway: ConformanceGateway, key: str | None = None) -> str:
        for known_key, known in self.gateways.items():
            if known is gateway and key in (None, known_key):
                return known_key
        key = key or uuid.uuid4().hex[:12]
        self.gateways[key] = gateway
        self.authorizations[key] = []
        return key

    def bind(self, run_id: str, attempt_id: str, binding: GatewayBinding) -> None:
        self.bindings[(run_id, attempt_id)] = binding

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/m/{key}/v1/chat/completions", self._chat)
        app.router.add_get("/m/{key}/v1/models", self._models)
        app.router.add_post(TOKEN_ROUTE.replace("{run_id}", "{run}"), self._token)
        app.router.add_route("*", "/{tail:.*}", self._other)
        return app

    async def _other(self, request: web.Request) -> web.Response:
        self.paths.append(request.path)
        return web.json_response({"error": {"message": "not served", "code": "not_found"}}, status=404)

    async def _models(self, request: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": [{"id": "engine-model", "object": "model"}]})

    async def _token(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {WORKER_CREDENTIAL}":
            return web.json_response({"error": {"code": "UNAUTHORIZED"}}, status=401)
        body = await request.json()
        # Node's body schema is strict: exactly orgId, attemptId and the broker's bindingProof (R38IDENT-FU-7).
        if not isinstance(body, dict) or set(body) != {"orgId", "attemptId", "bindingProof"}:
            return web.json_response({"error": {"code": "HTTP_BAD_REQUEST"}}, status=400)
        run_id, attempt_id = request.match_info["run"], str(body["attemptId"])
        # As Node, the proof is checked before any run is read: the lane's broker signs with the test binding key.
        if body["bindingProof"] != proof_for(str(body["orgId"]), run_id, attempt_id):
            return web.json_response({"error": {"code": "UNAUTHORIZED"}}, status=401)
        binding = self.bindings.get((run_id, attempt_id))
        if binding is None:
            return web.json_response({"error": {"code": "RUN_NOT_LEASED"}}, status=409)
        token = self.mint.mint(binding)
        self.issued.append(token)
        return web.json_response({"token": token, "expiresAt": int((time.time() + 600) * 1000)})

    async def _chat(self, request: web.Request) -> web.StreamResponse:
        key = request.match_info["key"]
        gateway = self.gateways.get(key)
        if gateway is None:
            return web.json_response({"error": {"message": "unknown model route"}}, status=404)
        self.authorizations[key].append(request.headers.get("Authorization", ""))
        if key in self.budget_spent:
            # E3's refusal once a run's budget is spent: 402 (OpenAI clients do not retry it), no event.
            error = {"message": "the run's model budget is spent", "type": "insufficient_quota", "code": "budget_exhausted"}
            return web.json_response({"error": error}, status=402, headers={"X-Gateway-Refusal": "budget_exhausted"})
        body = as_object(await request.json())
        self.images.setdefault(key, []).extend(_image_types(body))
        tools = json.dumps(body.get("tools"), sort_keys=True, separators=(",", ":"))
        self.tool_lists.setdefault(key, []).append(hashlib.sha256(tools.encode()).hexdigest())
        headers = {k.lower(): v for k, v in request.headers.items()}
        answer = await gateway._model(  # pyright: ignore[reportPrivateUsage]
            EgressRequest(url=str(request.url), method="POST", headers=headers, body=to_conformance(body))
        )
        reply = ModelReply.from_json(answer)
        if reply.error == CONTEXT_OVERFLOW:
            return web.json_response(OVERFLOW_ERROR, status=400)
        if not body.get("stream"):
            message: dict[str, Any] = {"role": "assistant", "content": reply.content or None}
            if reply.tool_calls:
                message["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in reply.tool_calls
                ]
            finish = "tool_calls" if reply.tool_calls else "stop"
            return web.json_response(
                {
                    "id": "chatcmpl-e0",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "engine-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": _chunks(reply)[-1]["usage"],
                }
            )
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        for index, chunk in enumerate(_chunks(reply)):
            if index == 1 and self.pauses.get(key):
                await asyncio.sleep(self.pauses[key])
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response


class RecordingGateway:
    """ASGI wrapper around ``ToolGatewayApp`` that keeps every ``Authorization`` the gateway received."""

    def __init__(self, app: ToolGatewayApp) -> None:
        self.app = app
        self.authorizations: list[str] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        if scope.get("type") == "http":
            for name, value in scope.get("headers", []):
                if name == b"authorization":
                    self.authorizations.append(value.decode("latin-1"))
        await self.app(scope, receive, send)


@dataclass
class Mount:
    upstream: str
    stop: asyncio.Event
    task: asyncio.Task[None]


@dataclass
class RealStack:
    """The Docker side (network, proxy container) for one module; the HTTP side is started per test."""

    client: Any  # docker.DockerClient
    suffix: str
    network: str
    proxy: str
    host_ip: str
    model_port: int
    gateway_port: int
    control_token: str
    mint: TokenMint = field(default_factory=lambda: TokenMint(SystemClock()))
    # P5.4b: the proxy's state store (a Redis of the stack's own) when the lane keeps its bindings durable.
    redis: str | None = None
    models: ModelAndNode = field(init=False)
    gateway: RecordingGateway = field(init=False)
    mounts: list[Mount] = field(default_factory=list[Mount])

    def __post_init__(self) -> None:
        self.models = ModelAndNode(self.mint)
        self.gateway = RecordingGateway(ToolGatewayApp(OrgBoundGatewayTokenVerifier(self.mint.source, SystemClock())))

    @property
    def model_base(self) -> str:
        return f"http://{self.host_ip}:{self.model_port}"

    @property
    def gateway_base(self) -> str:
        return f"http://{self.host_ip}:{self.gateway_port}{GATEWAY_MOUNT}"

    @property
    def control_url(self) -> str:
        info = self.client.api.inspect_container(self.proxy)
        return f"http://{info['NetworkSettings']['Networks'][self.network]['IPAddress']}:{EGRESS_CONTROL_PORT}"

    def model_upstream(self, gateway: ConformanceGateway) -> str:
        return f"{self.model_base}/m/{self.models.register(gateway)}"

    def proxy_logs(self) -> str:
        return self.client.api.logs(self.proxy).decode("utf-8", "replace")

    def refusals_for(self, sandbox_id: str) -> list[str]:
        return [
            line for line in self.proxy_logs().splitlines() if "egress.refused" in line and f"sandboxId={sandbox_id}" in line
        ]

    def outside_attempts(self, sandbox_id: str) -> list[str]:
        """CF-17: refusals of this sandbox's requests for a host other than the two gateways (a vendor's update,
        telemetry or phone-home host). A gateway request refused for its credentials (Claude Code's unauthenticated
        preconnect, FB-2) is not one."""
        outside: list[str] = []
        for line in self.refusals_for(sandbox_id):
            host = re.search(r"\bhost=(\S+)", line)
            if host is None and "reason=placeholder_missing" in line:
                continue
            if host is None or host.group(1) not in GATEWAY_ALIASES:
                outside.append(line)
        return outside

    @contextlib.asynccontextmanager
    async def serving(self) -> AsyncIterator[RealStack]:
        """The test's HTTP side: model + Node on aiohttp, the tool gateway on uvicorn."""
        runner = web.AppRunner(self.models.app(), access_log=None, handler_cancellation=True)
        await runner.setup()
        site = web.TCPSite(runner, self.host_ip, self.model_port, reuse_address=True)
        await site.start()
        api = FastAPI()
        api.mount(GATEWAY_MOUNT, self.gateway)
        server = uvicorn.Server(
            uvicorn.Config(api, host=self.host_ip, port=self.gateway_port, log_level="warning", lifespan="off")
        )
        task = asyncio.ensure_future(server.serve())
        while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
            await asyncio.sleep(0.01)
        try:
            yield self
        finally:
            for mount in self.mounts:
                mount.stop.set()
                with contextlib.suppress(Exception):
                    await mount.task
            self.mounts.clear()
            server.should_exit = True
            await task
            await runner.cleanup()

    async def mount(self, endpoint: Any, binding: GatewayBinding) -> str:  # noqa: ANN401 - a ToolEndpoint
        """Serve ``endpoint`` on the tool gateway from a task of its own (the MCP session manager's
        task group must be exited by the task that entered it); returns the proxy's upstream."""
        ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        async def hold() -> None:
            async with self.gateway.app.serving(endpoint, binding) as key:
                ready.set_result(f"{self.gateway_base}/{key}")
                await stop.wait()

        task = asyncio.ensure_future(hold())
        upstream = await ready
        self.mounts.append(Mount(upstream, stop, task))
        return upstream


@dataclass(frozen=True, slots=True)
class GatewayRequest:
    method: str
    path: str
    authorization: str
    body: Mapping[str, JsonValue]
    # Anthropic Messages betas the engine asked for (E9 RX-CC3); empty on the other wires.
    betas: str = ""


@dataclass
class ModelGatewayLane:
    """E3's real ``ModelGateway`` (E3b's Responses route too) between the proxy and the scripted model.

    Each conformance gateway gets its own pinned route, whose real ``OpenAITransport`` calls that
    gateway's scripted model on ``ModelAndNode`` as an OpenAI provider with an org key; the run's grant
    sits in the in-memory adapter of ``RunModelGrants`` and tokens verify with the stack's mint (Node's
    keys in production)."""

    stack: RealStack
    port: int
    grants: InMemoryRunModelGrants = field(default_factory=InMemoryRunModelGrants)
    resolver: StaticModelAccessResolver = field(default_factory=StaticModelAccessResolver)
    # P6.5's ledger (in memory): the gateway reserves before every call and refuses with 402 when it cannot.
    ledger: InMemoryBudgetLedger = field(default_factory=lambda: InMemoryBudgetLedger(SystemClock()))
    requests: list[GatewayRequest] = field(default_factory=list[GatewayRequest])
    refused: list[tuple[str, int, str]] = field(default_factory=list[tuple[str, int, str]])
    # How long a silent stream waits before the gateway writes its wire's keepalive (ADR-GW-01 D1).
    heartbeat_s: float = HEARTBEAT_S

    @property
    def upstream(self) -> str:
        return f"http://{self.stack.host_ip}:{self.port}"

    def pin(
        self, gateway: ConformanceGateway, route: PinnedRoute, *, budget: RunBudget | None = None, key: str | None = None
    ) -> None:
        """Route ``gateway``'s run to its scripted model; the run's token names ``route``."""
        key = self.stack.models.register(gateway, key)
        transport = OpenAITransport(api_key=PROVIDER_KEY, model=route.model, base_url=f"{self.stack.model_base}/m/{key}/v1")
        binding = gateway.binding
        pinned = budget or RunBudget(max_cost_usd=None, max_tokens=None)
        self.resolver.add(binding.tenant, route, org_key_route(route, transport))
        self.grants.register(
            RunModelGrant(
                tenant=binding.tenant,
                run_id=binding.run_id,
                routes=(route,),
                models=neutral_models((route,)),
                budget=pinned,
            )
        )
        cap = Cap(usd_micros(pinned.max_cost_usd) if pinned.max_cost_usd is not None else None, pinned.max_tokens)
        self.ledger.pin(RunAccountRef(binding.tenant, binding.run_id), PinnedBudget(cap))

    @contextlib.asynccontextmanager
    async def serving(self) -> AsyncIterator[ModelGatewayLane]:
        gateway = ModelGateway(
            verifier=OrgBoundGatewayTokenVerifier(self.stack.mint.source, SystemClock()),
            grants=self.grants,
            resolver=self.resolver,
            budget=self.ledger,
            clock=SystemClock(),
            ids=RandomIds(),
        )

        @web.middleware
        async def record(request: web.Request, handler: Any) -> web.StreamResponse:  # noqa: ANN401
            raw = await request.read()
            body: Mapping[str, JsonValue] = {}
            with contextlib.suppress(ValueError):
                body = as_object(json.loads(raw)) if raw else {}
            self.requests.append(
                GatewayRequest(
                    request.method,
                    request.path,
                    request.headers.get("Authorization", ""),
                    body,
                    request.headers.get("anthropic-beta", ""),
                )
            )
            response = await handler(request)
            if response.status >= 400:
                self.refused.append((request.path, response.status, response.headers.get("X-Gateway-Refusal", "")))
            return response

        app = create_model_gateway_app(gateway, heartbeat_s=self.heartbeat_s)
        app.middlewares.append(record)
        runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
        await runner.setup()
        await web.TCPSite(runner, self.stack.host_ip, self.port, reuse_address=True).start()
        try:
            yield self
        finally:
            await runner.cleanup()


def start_stack(
    *, upstreams: Callable[[RealStack], Mapping[str, str]] | None = None, egress_state: bool = False
) -> RealStack:
    """``upstreams`` (C-13) is called once the upstream bridge exists, with the stack, and returns proxy
    environment overrides (``NODE_URL``, ``WORKER_CREDENTIAL``, ``EGRESS_UPSTREAMS``): what the caller
    serves on ``host_ip`` instead of the scripted model and fake Node. ``egress_state`` (P5.4b) gives the
    proxy a Redis on the bridge and keeps its bindings and grants there, as ``app.egress_proxy_main`` does."""
    import docker

    client = docker.from_env()
    _ = docker_session.reap_stale(client, STACK_SUITE)
    labels = {**OWNER, **docker_session.labels(STACK_SUITE)}
    suffix = uuid.uuid4().hex[:8]
    network = f"pxh-e0-up-{suffix}"
    host_ip = create_rig_network(client, network, labels=labels)
    stack = RealStack(
        client=client,
        suffix=suffix,
        network=network,
        proxy=f"pxh-e0-proxy-{suffix}",
        host_ip=host_ip,
        model_port=_free_port(host_ip),
        gateway_port=_free_port(host_ip),
        control_token=uuid.uuid4().hex,
    )
    host_config = client.api.create_host_config(
        binds={
            str(PYTHON_ROOT): {"bind": "/src", "mode": "ro"},
            sysconfig.get_paths()["purelib"]: {"bind": "/site", "mode": "ro"},
        },
        network_mode=network,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        read_only=True,
        mem_limit=512 * 1024 * 1024,
        tmpfs={"/tmp": "size=16m"},
    )
    try:
        environment = {
            "EGRESS_CONTROL_TOKEN": stack.control_token,
            # The control plane listens on the upstream bridge only, never on a sandbox's network (E2E4).
            "EGRESS_CONTROL_HOST": EGRESS_CONTROL_ALIAS,
            "SANDBOX_EGRESS_SUBNETS": EGRESS_POOL,
            "EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"{stack.model_base}/m/{STATIC_MODEL_KEY}"}),
            "NODE_URL": stack.model_base,
            "WORKER_CREDENTIAL": WORKER_CREDENTIAL,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if upstreams is not None:
            environment.update(upstreams(stack))
        if egress_state:
            stack.redis = f"pxh-e0-redis-{suffix}"
            redis_config = client.api.create_host_config(
                network_mode=network, cap_drop=["ALL"], security_opt=["no-new-privileges:true"], read_only=True,
                mem_limit=128 * 1024 * 1024, tmpfs={"/data": "size=16m"},
            )
            created_redis = client.api.create_container(
                REDIS_IMAGE, command=["redis-server", "--save", "", "--appendonly", "no"], name=stack.redis,
                user="1000:1000", labels=labels, host_config=redis_config,
            )
            client.api.start(created_redis["Id"])
            environment |= {
                "EGRESS_TEST_REDIS_URL": f"redis://{stack.redis}:6379/0",
                "EGRESS_TEST_STATE_KEY": uuid.uuid4().hex,
            }
        created = client.api.create_container(
            HELPER_IMAGE,
            command=["python", "-c", _PROXY],
            name=stack.proxy,
            user="1000:1000",
            environment=environment,
            labels=labels,
            host_config=host_config,
            networking_config=control_alias_config(client, network),
        )
        client.api.start(created["Id"])
        _wait_healthy(stack.control_url)
    except BaseException:
        stop_stack(stack)
        raise
    return stack


def _wait_healthy(url: str, *, timeout_s: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"egress proxy at {url} never became healthy")
        time.sleep(0.3)


def stop_stack(stack: RealStack) -> None:
    api = stack.client.api
    for name in (stack.proxy, stack.redis):
        if name is not None:
            with contextlib.suppress(Exception):
                api.remove_container(name, force=True, v=True)
    with contextlib.suppress(Exception):
        api.remove_network(stack.network)
    stack.client.close()


# C1.03: the runtime the lane's engine sandboxes run on. Unset or ``auto``, the strongest the daemon offers, as the
# controller picks for a run (``engine_mode``); ``runc`` or ``runsc`` pins one, so one host can compare the two. The
# words are run.sh's ``E2E_SANDBOX_RUNTIME`` ones, so one switch names a runtime on every lane (R31GVENG-FU-4);
# ``gvisor`` stays an alias of ``runsc``.
RUNTIME_ENV = "HARNESS_TEST_SANDBOX_RUNTIME"
_RUNTIMES = {"runc": RuntimeMode.DOCKER_HARDENED, "runsc": RuntimeMode.GVISOR, "gvisor": RuntimeMode.GVISOR}


@functools.cache
def daemon_has_runsc() -> bool:
    try:
        import docker

        client = docker.from_env()
        try:
            return "runsc" in (client.info().get("Runtimes") or {})
        finally:
            client.close()
    except Exception:
        return False


def lane_mode() -> RuntimeMode:
    """The lane's sandbox runtime (``HARNESS_TEST_SANDBOX_RUNTIME``); asking for gVisor on a daemon without
    ``runsc`` is the caller's mistake, so the broker's own refusal says so rather than a silent runc run."""
    named = (os.environ.get(RUNTIME_ENV) or "").strip().lower()
    if named and named != "auto":
        if named not in _RUNTIMES:
            raise ValueError(f"{RUNTIME_ENV}={named!r}: expected one of {sorted({'auto', *_RUNTIMES})}")
        return _RUNTIMES[named]
    return RuntimeMode.GVISOR if daemon_has_runsc() else RuntimeMode.DOCKER_HARDENED


# P5.9a: the lanes run with the broker's warm pool when this names a ceiling (C-13, E1's real lane, G4).
WARM_POOL_ENV = "HARNESS_TEST_WARM_POOL"


def lane_warm_pool(images: Iterable[str], labels: Mapping[str, str], ceiling: int | None = None) -> WarmPoolConfig | None:
    """The lane's pool: shapes learned from the lane's own acquires of ``images`` (or primed with ``prime_pool``);
    ``ceiling`` ``None`` reads ``HARNESS_TEST_WARM_POOL``."""
    if ceiling is None:
        ceiling = int(os.environ.get(WARM_POOL_ENV) or 0)
    if ceiling <= 0:
        return None
    return WarmPoolConfig(ceiling=ceiling, learn_images=frozenset(images), labels=dict(labels))


async def prime_pool(broker: DockerSandboxBroker, spec: SandboxSpec) -> int:
    """Pre-create ``spec``'s shape now, as a broker that served it before would; the number created."""
    if not broker.pool.enabled:
        return 0
    broker.pool.learn(spec)
    return await broker.fill_warm()


async def engine_broker(
    stack: RealStack,
    namespace: str,
    prefix: str,
    *,
    image: str = IMAGE,
    pool_ceiling: int | None = None,
    binary_pins: Mapping[str, Sequence[PinnedBinary]] | None = None,
    data_volume: str | None = None,
) -> tuple[DockerSandboxBroker, DockerClientProvider]:
    provider = DockerClientProvider(max_workers=16)
    clock = ManualClock()
    bindings = RemoteEgressBindings(stack.control_url, StaticBearerAuth(stack.control_token))
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({image}),
        admit_candidate_images=lane_admits_candidates(),
        label_namespace=namespace,
        volume_prefix=prefix,
        warm_pool=lane_warm_pool({image}, {**OWNER, "pxh.lane": stack.suffix}, pool_ceiling),
        binary_pins=binary_pins,
        # WP100-FU-7: where persistent mounts (a shared cache) live; none, a spec naming one is refused.
        data_volume=data_volume,
        egress=DockerEgressWiring(
            provider=provider,
            clock=clock,
            proxy_container=stack.proxy,
            bindings=bindings,
            pool=SubnetPool(EGRESS_POOL),
            # Signed as the production broker signs, under the key journal-http-server.ts verifies (R38IDENT-FU-7).
            attester=TEST_ATTESTER,
            network_prefix=f"{prefix}-net-",
        ),
    )
    if lane_mode() is RuntimeMode.DOCKER_HARDENED:
        # A runc lane on a runsc host: the controller picks the strongest runtime offered, so offer only runc.
        caps = broker.capabilities
        broker._caps = replace(caps, runtimes=caps.runtimes - {"runsc"})  # pyright: ignore[reportPrivateUsage]
    return broker, provider


__all__ = [
    "IMAGE",
    "PROVIDER_KEY",
    "GatewayRequest",
    "ModelGatewayLane",
    "STATIC_MODEL_KEY",
    "OPENCODE",
    "OWNER",
    "ModelAndNode",
    "RealStack",
    "engine_broker",
    "engine_image",
    "RUNTIME_ENV",
    "daemon_has_runsc",
    "lane_available",
    "lane_mode",
    "lane_warm_pool",
    "prime_pool",
    "start_stack",
    "stop_stack",
    "to_conformance",
]

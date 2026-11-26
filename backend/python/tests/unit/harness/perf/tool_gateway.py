"""PF-05's rig: what the tool gateway adds to an engine's tool call (auth, grant, policy, audit), the tool excluded.

The gateway is production's path end to end, in one worker process:

* ``ToolGatewayApp`` under uvicorn, verifying Node's gateway tokens with Node's published keys;
* each run is admitted on Node (``journal-http-server.ts``: the journal service and routes; MongoDB-backed when
  started so) and driven by the real ``EngineKernel``: the run's ``RunToolEndpoint`` is mounted on the gateway behind
  the kernel's tracking endpoint, every call is approved by the run's policy and audited through the attempt's
  fenced commits on Node's journal (the intent, the outcome, and the result's activity line when the engine announced
  the call first, as engines do);
* the engine is a paced one in a fake sandbox: it announces each call on its event stream, then calls the null tool
  over MCP with the token Node issues its attempt (what the egress proxy injects), at a fixed rate with a bounded
  number in flight, until the rig stops it.

The tool is a null read. Each request is timed at the gateway's ASGI boundary, and the tool's own time inside it:

    overhead = (response sent - request in) - tool

The engines' HTTP clients share the worker's event loop here (in production they are other processes), so a number
from this rig is an upper bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import uvicorn
from fastapi import FastAPI

from app.harness.engines.presenter import PipesHubEngineRunPresenter
from app.harness.gateway.host import HttpToolGatewayHost
from app.harness.gateway.keys import parse_published
from app.harness.gateway.mcp import ToolGatewayApp
from app.harness.gateway.tokens import NodeGatewayTokens
from app.harness.journal_node import NodeJournal
from harness_core.access.types import PinnedRoute
from harness_core.clock import SystemClock
from harness_core.effects import ToolExecution
from harness_core.engines.controller import TICK_S
from harness_core.engines.durable.kernel import FLUSH_INTERVAL_S
from harness_core.engines.events import ToolCallSeen, TurnCompleted
from harness_core.engines.jsonview import as_object, as_str
from harness_core.engines.stream import TurnSeq
from harness_core.gateway import GatewayTokenVerifier
from harness_core.gateway.memory import StaticGatewayKeySource
from harness_core.interactions.journal import POLL_S
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.durable import (
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from tests.unit.harness.engines.durable_stack import engine_spec as durable_engine_spec
from tests.unit.harness.gateway.binding_keys import proof_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ToolContext
    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.types import EngineLaunch, EngineTask, FollowUp
    from harness_core.gateway.ports import ToolEndpoint
    from harness_core.gateway.types import GatewayBinding
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.testing.sandbox.egress import EgressRequest

MOUNT = "/harness/tool-gateway"
NULL_TOOL = offered("lookup")
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LEASE_MS = 120_000


@dataclass
class _Call:
    tool_s: float = 0.0
    method: str = ""


_CURRENT: contextvars.ContextVar[_Call | None] = contextvars.ContextVar("pf05_call", default=None)


@dataclass(frozen=True, slots=True)
class Timed:
    method: str
    status: int
    started: float
    gateway_ms: float
    tool_ms: float

    @property
    def overhead_ms(self) -> float:
        return self.gateway_ms - self.tool_ms


class TimedGateway:
    """The gateway's ASGI app, each request timed from its start to the last byte of its response."""

    def __init__(self, app: ToolGatewayApp) -> None:
        self.app = app
        self.timed: list[Timed] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        call = _Call()
        reset = _CURRENT.set(call)
        started = time.perf_counter()
        ended: float | None = None
        status = 0

        async def receiving() -> Any:  # noqa: ANN401
            message = await receive()
            if message.get("type") == "http.request" and not call.method:
                with contextlib.suppress(ValueError, AttributeError):
                    call.method = str(json.loads(message.get("body") or b"{}").get("method", ""))
            return message

        async def sending(message: dict[str, Any]) -> None:
            nonlocal ended, status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                ended = time.perf_counter()

        try:
            await self.app(scope, receiving, sending)
        finally:
            _CURRENT.reset(reset)
            finished = ended if ended is not None else time.perf_counter()
            self.timed.append(Timed(call.method, status, started, (finished - started) * 1000, call.tool_s * 1000))


async def null_tool(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    started = time.perf_counter()
    execution = ToolExecution(status="ok", content="ok")
    timed = _CURRENT.get()
    if timed is not None:
        timed.tool_s += time.perf_counter() - started
    return execution


async def mcp(http: httpx.AsyncClient, url: str, token: str, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
    """One stateless MCP request; the JSON-RPC result, from a JSON or an event-stream answer."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": dict(params)}
    answer = await http.post(url, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}, json=body)
    if answer.status_code != 200:
        raise AssertionError(f"the gateway answered {method} with {answer.status_code}: {answer.text[:300]}")
    text = answer.text
    if answer.headers.get("content-type", "").startswith("text/event-stream"):
        text = next(line[6:] for line in text.splitlines() if line.startswith("data: "))
    return as_object(json.loads(text)).get("result")


@dataclass(frozen=True, slots=True)
class Mounted:
    run_id: str
    url: str
    token: str


class LoadHost:
    """The worker's ``HttpToolGatewayHost``, telling the engines which URL and token reach their run's tools; it also
    answers the tools/list ``FakeEngine`` sends through its sandbox's egress when its session opens."""

    def __init__(self, gateway: ToolGatewayApp, base: str, tokens: NodeGatewayTokens) -> None:
        self._inner = HttpToolGatewayHost(gateway, base_url=base)
        self.base = base
        self._tokens = tokens
        self._by_url: dict[str, str] = {}
        self.mounted: dict[str, asyncio.Future[Mounted]] = {}
        self.http = httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=1000))

    def waiting(self, run_id: str) -> asyncio.Future[Mounted]:
        return self.mounted.setdefault(run_id, asyncio.get_running_loop().create_future())

    @contextlib.asynccontextmanager
    async def serve(self, endpoint: ToolEndpoint, binding: GatewayBinding) -> AsyncIterator[str]:
        async with self._inner.serve(endpoint, binding) as url:
            run_id, attempt_id = getattr(binding, "run_id", ""), getattr(binding, "attempt_id", "")
            issued = await self._tokens.for_attempt(binding.tenant, run_id, attempt_id, proof_for(binding.tenant, run_id, attempt_id))
            self._by_url[url] = issued.value
            waiting = self.waiting(run_id)
            if not waiting.done():
                waiting.set_result(Mounted(run_id, f"{url}/mcp", issued.value))
            try:
                yield url
            finally:
                _ = self._by_url.pop(url, None)

    async def handle(self, request: EgressRequest) -> JsonValue:
        token = next(t for base, t in self._by_url.items() if request.url.startswith(base + "/"))
        body = as_object(request.body)
        result = as_object(await mcp(self.http, request.url, token, as_str(body.get("method")), {}))
        return {"tools": [{"name": as_str(as_object(t).get("name")), "description": ""} for t in result.get("tools") or []]}

    async def aclose(self) -> None:
        await self.http.aclose()


@dataclass
class Pace:
    """Each run's engine: ``rate_per_s`` calls started a second, at most ``parallel`` in flight, until ``stop``."""

    rate_per_s: float
    parallel: int = 4
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    # What each engine saw: (started, ms) per call, and the calls that failed.
    latency: list[tuple[float, float]] = field(default_factory=list[tuple[float, float]])
    failed: list[str] = field(default_factory=list[str])


class PacedSession:
    """A ``FakeEngine`` session whose turn is the paced calls: each announced on the engine's stream (the kernel has
    taken the event before the call goes out), then made over MCP."""

    def __init__(self, inner: Any, host: LoadHost, run_id: str, pace: Pace) -> None:  # noqa: ANN401
        self._inner = inner
        self._host = host
        self._run_id = run_id
        self._pace = pace

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)

    async def _call(self, mounted: Mounted, seq: int) -> bool:
        started = time.perf_counter()
        try:
            result = as_object(
                await mcp(self._host.http, mounted.url, mounted.token, "tools/call",
                          {"name": NULL_TOOL.name, "arguments": {"seq": seq}})
            )
        except (AssertionError, httpx.HTTPError) as exc:
            self._pace.failed.append(f"{type(exc).__name__}: {str(exc)[:200]}")
            return False
        self._pace.latency.append((started, (time.perf_counter() - started) * 1000))
        if result.get("isError") is True:
            self._pace.failed.append(f"isError: {json.dumps(result)[:200]}")
            return False
        return True

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        mounted = await self._host.waiting(self._run_id)
        seq = TurnSeq(task.turn_id)
        loop = asyncio.get_running_loop()
        announced: asyncio.Queue[tuple[EngineEvent, asyncio.Future[None]] | None] = asyncio.Queue()

        async def announce(event: EngineEvent) -> None:
            taken = loop.create_future()
            await announced.put((event, taken))
            await taken

        async def one(index: int) -> None:
            call_id = f"call_{index}"
            await announce(ToolCallSeen(seq.next(), call_id, NULL_TOOL.name, "started"))
            ok = await self._call(mounted, index)
            await announce(ToolCallSeen(seq.next(), call_id, NULL_TOOL.name, "completed" if ok else "failed"))

        async def drive() -> None:
            slots = asyncio.Semaphore(self._pace.parallel)
            calls: list[asyncio.Task[None]] = []
            start = time.perf_counter()
            index = 0
            while not self._pace.stop.is_set():
                delay = start + index / self._pace.rate_per_s - time.perf_counter()
                if delay > 0:
                    with contextlib.suppress(TimeoutError):
                        _ = await asyncio.wait_for(self._pace.stop.wait(), delay)
                    continue
                await slots.acquire()
                call = asyncio.ensure_future(one(index))
                call.add_done_callback(lambda _: slots.release())
                calls.append(call)
                index += 1
            _ = await asyncio.gather(*calls)
            await announced.put(None)

        driver = asyncio.ensure_future(drive())
        try:
            while (item := await announced.get()) is not None:
                event, taken = item
                yield event
                taken.set_result(None)
            await driver
            yield TurnCompleted(seq.next(), "completed", "done")
        finally:
            if not driver.done():
                _ = driver.cancel()


class PacedEngine(ScriptedEngineAdapter):
    """``FakeEngine`` (its launch, sandbox files and the tools/list at open), driving its turn at ``pace``."""

    def __init__(self, host: LoadHost, pace: Pace) -> None:
        super().__init__()
        self._host = host
        self._pace = pace

    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> Any:  # noqa: ANN401
        inner = await super().open(sandbox, launch, restore, correlation=correlation)
        return PacedSession(inner, self._host, dict(correlation or {})["runId"], self._pace)


def rig_world(host: LoadHost, pace: Pace) -> EngineWorld:
    """The runs' world: the null tool, the paced engine, and the controller as a worker runs it, its turn's ticker
    every ``TICK_S`` on the event loop's own clock (the unit-test world's 1 ms ticks were a thousand wake-ups a
    second per run on the loop the gateway shares, which no worker has)."""
    return EngineWorld(
        host=host,  # type: ignore[arg-type]
        presenter=PipesHubEngineRunPresenter(),  # type: ignore[arg-type]
        tools=(NULL_TOOL,),
        handlers={NULL_TOOL.name: null_tool},
        adapter=PacedEngine(host, pace),
        lease_ms=LEASE_MS,
        # The route the pinned spec names: the run's binding then carries the digests Node's tokens name.
        routes=(PinnedRoute("openaicompatible", "local-model"),),
        tick_s=TICK_S,
        controller_sleep=asyncio.sleep,
    )


def run_spec() -> dict[str, Any]:
    """The spec Node's admission pins for an engine run granted the null tool (E1's lane), on ``FakeEngine``."""
    spec = durable_engine_spec(tools=(NULL_TOOL.effect.tool_id,))
    pinned = engine_run_spec(engine_profile(), tools=(NULL_TOOL,)).model_dump(mode="json", by_alias=True)
    spec["executor"] = pinned["executor"]
    return spec


@dataclass(frozen=True)
class JournalNode:
    """A running journal server (``_NodeServer`` in memory, or ``MongoNode``) and how its runs are named: Mongo
    mode takes object ids for the org and its sessions."""

    server: Any  # base_url, credential, control
    org: str
    session_for: Callable[[str], str]

    @property
    def base_url(self) -> str:
        return str(self.server.base_url)

    @property
    def credential(self) -> str:
        return str(self.server.credential)

    @property
    def proxy_credential(self) -> str:
        return str(self.server.proxy_credential)

    @property
    def control(self) -> httpx.Client:
        return self.server.control


@dataclass
class ToolGatewayRig:
    node: JournalNode
    gateway: TimedGateway
    host: LoadHost
    journal: NodeJournal
    runs: list[asyncio.Task[Any]] = field(default_factory=list[asyncio.Task[Any]])
    run_ids: list[str] = field(default_factory=list[str])

    async def run(self, count: int, pace: Pace) -> None:
        """``count`` runs admitted, claimed and driven by the kernel, their engines calling at ``pace``; returns once
        every run has mounted its tools (the calls are under way)."""
        world = rig_world(self.host, pace)
        kernel = world.kernel(
            journal=self.journal,
            clock=SystemClock(),
            poll_s=POLL_S,
            flush_interval_s=FLUSH_INTERVAL_S,
            sleep=asyncio.sleep,
            renew_interval_s=LEASE_MS / 3000,
        )
        spec = run_spec()
        mounted: list[asyncio.Future[Mounted]] = []
        for _ in range(count):
            run_id = f"pf05-{uuid.uuid4().hex[:12]}"
            body = {
                "runId": run_id, "sessionId": self.node.session_for(run_id), "inputText": "look things up", "spec": spec,
                "orgId": self.node.org,
            }
            _ = self.node.control.post("/admit", json=body).raise_for_status()
            claim = await self.journal.claim(run_id, worker_id="pf05-worker", org_id=self.node.org)
            assert claim is not None, f"{run_id} was not claimable"
            mounted.append(self.host.waiting(run_id))
            self.run_ids.append(run_id)
            self.runs.append(asyncio.ensure_future(kernel.run_claimed(claim, claim_sent_at=SystemClock().suspend_aware())))
        done, _ = await asyncio.wait([*mounted, *self.runs], timeout=60, return_when=asyncio.FIRST_COMPLETED)
        while not all(m.done() for m in mounted):
            ended = [t for t in self.runs if t.done()]
            if ended:
                raise AssertionError(f"a run ended before its tools were mounted: {ended[0].result()!r}")
            done, _ = await asyncio.wait([*mounted, *self.runs], timeout=60, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise AssertionError("the runs did not mount their tools in 60 s")

    async def finish(self, pace: Pace) -> list[Any]:
        """The engines stop calling and end their turns; the runs' dispositions."""
        pace.stop.set()
        return list(await asyncio.wait_for(asyncio.gather(*self.runs, return_exceptions=True), 120))


def _sync_clock(control: httpx.Client) -> None:
    """Node's journal server keeps a manual clock: on the wall's, so its tokens verify on this process's clock."""
    now = int(control.get("/now").json()["now"])
    wall = int(time.time() * 1000)
    if wall > now:
        _ = control.post("/advance", json={"seconds": (wall - now) / 1000}).raise_for_status()


@contextlib.asynccontextmanager
async def tool_gateway_rig(node: JournalNode) -> AsyncIterator[ToolGatewayRig]:
    _sync_clock(node.control)
    keys = StaticGatewayKeySource(parse_published(node.control.get("/gateway-public-keys").json()))  # type: ignore[arg-type]
    gateway = TimedGateway(ToolGatewayApp(GatewayTokenVerifier(keys, SystemClock())))

    class _Credential:
        async def worker_credential(self) -> str:
            return node.credential

    class _ProxyCredential:
        # ADR-CRED-01: only the egress proxy's credential mints a gateway token.
        async def worker_credential(self) -> str:
            return node.proxy_credential

    api = FastAPI()
    api.mount(MOUNT, gateway)
    server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    task = asyncio.ensure_future(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
        await asyncio.sleep(0.01)
    port = int(server.servers[0].sockets[0].getsockname()[1])
    tokens = NodeGatewayTokens(base_url=node.base_url, credentials=_ProxyCredential())
    host = LoadHost(gateway.app, f"http://127.0.0.1:{port}{MOUNT}", tokens)
    journal = NodeJournal(base_url=node.base_url, credentials=_Credential(), lease_ms=LEASE_MS)
    rig = ToolGatewayRig(node, gateway, host, journal)
    try:
        yield rig
    finally:
        for run in rig.runs:
            if not run.done():
                _ = run.cancel()
        _ = await asyncio.gather(*rig.runs, return_exceptions=True)
        await host.aclose()
        await journal.aclose()
        server.should_exit = True
        await task


__all__ = [
    "NULL_TOOL",
    "JournalNode",
    "Mounted",
    "Pace",
    "Timed",
    "ToolGatewayRig",
    "mcp",
    "tool_gateway_rig",
]

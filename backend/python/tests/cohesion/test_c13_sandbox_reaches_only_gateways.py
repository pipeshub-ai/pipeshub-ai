"""C-13 (the security half of gate G1): a sandbox reaches only the gateways, and holds no secret.

A fake engine (``c13_sandbox/fake_engine.py``, stdlib Python in ``pxh-sandbox:x1``)
runs in a real Docker sandbox behind P5.4's proxy container (E0's real-lane rig,
``tests/unit/harness/engines/real_stack.py``). It calls a granted tool through the
proxy → E2's ``ToolGatewayApp`` → ``RunToolEndpoint`` → Invoker → ``EffectRunner``,
and the model through the proxy → E3's ``ModelGateway`` (chat completions and E3b's
Responses route) → the real ``OpenAITransport`` → a fake provider. The proxy gets
every gateway token from Node's real authority (``GatewayTokenService`` behind the
internal router, in the journal HTTP test server), and both gateways verify them
with Node's published keys; E3 reads the run's grant from Node with that token.

Two runs, A and B, each with its own sandbox. B runs the happy path and stays
alive; A runs it, is scanned, then tries what a hostile engine would.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import pytest
from aiohttp import web

from app.harness.access.grants import NodeRunModelGrants
from app.harness.gateway.keys import parse_published
from app.harness.gateway.mcp import ToolGatewayApp
from app.harness.journal_node import NodeJournal
from harness_core.access.memory import StaticModelAccessResolver
from harness_core.access.spend import budget_tokens
from harness_core.access.types import PinnedRoute, route_digest
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from harness_core.clock import SystemClock
from harness_core.contracts import dump_contract
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import EffectRunner, RequestEffectLog, ToolExecution
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.gateway import (
    InMemoryCitationSink,
    RunAttemptBinding,
    RunToolEndpoint,
    grant_digest,
)
from harness_core.gateway.memory import StaticGatewayKeySource
from harness_core.gateway.model_gateway import ModelGateway
from harness_core.gateway.model_server import create_model_gateway_app
from harness_core.gateway.token import GatewayTokenVerifier
from harness_core.ids import RandomIds
from harness_core.invoker import CatalogTool, InMemoryToolCatalog, InvocationScope
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.transports.openai import OpenAITransport
from harness_core.sandbox.egress.types import EGRESS_PROXY_PORT
from harness_core.sandbox.streams import iter_bytes
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)
from harness_core.security.token_patterns import VENDOR_TOKEN_PATTERNS
from harness_core.testing.model_gateway import org_key_route
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.testing.sandbox.contract import WORK, tar_of
from tests.support.loopback_client import loopback_client
from tests.unit.agent_loop_lib.sandbox.contract.conftest import skip_without_runsc
from tests.unit.harness.credentials.test_leak import (
    _assert_clean,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness.engines.real_stack import (
    OWNER,
    RecordingGateway,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    lane_available,
    prime_pool,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks
from tests.unit.harness.test_node_journal_contract import (
    NODE_APP,
    _Credential,  # pyright: ignore[reportPrivateUsage]
    _NodeServer,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness_core.gateway.test_model_server import FakeProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.effects import EffectCall, ToolContext
    from harness_core.journal.types import Claim
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.sandbox.ports import ProcessHandle, SandboxHandle
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

SANDBOX_IMAGE = "pxh-sandbox:x1"
SCRIPTS = Path(__file__).parent / "c13_sandbox"
ORG = "org-contract"
TOOL, UNGRANTED = "record_note", "delete_everything"
TOOL_ID = tool_id_for(TOOL, source="c13", instance="inst-1")
UNGRANTED_ID = tool_id_for(UNGRANTED, source="c13", instance="inst-1")
ROUTE = PinnedRoute("openai", "gpt-c13")
# Vendor-shaped on purpose: P2.4a's helper must also find them by shape.
PROVIDER_KEY = "sk-proj-" + "C13ProviderOrgKey0123456789abcdef"
CONNECTOR_SECRET = "ghp_" + "C13ConnectorSecret0123456789abcdefghij"
MODEL_URL = f"http://model-gateway:{EGRESS_PROXY_PORT}/v1"
TOOLS_URL = f"http://tool-gateway:{EGRESS_PROXY_PORT}/mcp"
PROXY_URL = f"http://egress-proxy:{EGRESS_PROXY_PORT}"
JWT = next(p.pattern for p in VENDOR_TOKEN_PATTERNS if p.kind == "jwt")
EXPECTED_REFUSALS = {
    "foreign_token": "token_replayed",
    "foreign_placeholder": "placeholder_foreign",
    "vendor_key_header": "forbidden_pattern",
    "no_authorization": "placeholder_missing",
    "model_placeholder_to_tools": "placeholder_wrong_destination",
    "off_allowlist_0": "destination_not_allowed",
    "off_allowlist_1": "destination_not_allowed",
}


@dataclass
class Lane:
    """The module's Docker side and Node; everything HTTP is started inside the test."""

    stack: RealStack
    node: _NodeServer
    e3_port: int
    canary_port: int


@pytest.fixture(scope="module")
def lane() -> Iterator[Lane]:
    available, reason = lane_available(SANDBOX_IMAGE)
    if not available:
        pytest.skip(reason)
    if not (NODE_APP / "node_modules").is_dir() or shutil.which("npx") is None:
        pytest.skip("the Node workspace is not installed (npm ci)")
    started: dict[str, Any] = {}

    def upstreams(stack: RealStack) -> Mapping[str, str]:
        # Node listens where the proxy container can reach it: the upstream bridge's host address.
        node = started["node"] = _NodeServer(host=stack.host_ip)
        started["e3"], started["canary"] = _free_port(stack.host_ip), _free_port(stack.host_ip)
        return {
            "NODE_URL": node.base_url,
            "WORKER_CREDENTIAL": node.proxy_credential,
            "EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"http://{stack.host_ip}:{started['e3']}"}),
        }

    try:
        stack = start_stack(upstreams=upstreams)
    except BaseException:
        if "node" in started:
            started["node"].stop()
        raise
    try:
        yield Lane(stack, started["node"], started["e3"], started["canary"])
    finally:
        stop_stack(stack)
        started["node"].control.close()
        started["node"].stop()


class CountingEffectLog(RequestEffectLog):
    """The request effect log, keeping every intent and outcome (it keeps only the latest per key)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[tuple[str, str]] = []

    @override
    async def intent(self, call: EffectCall) -> None:
        self.records.append(("intent", call.effect.tool_id))
        await super().intent(call)

    @override
    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None:
        self.records.append(("outcome", call.effect.tool_id))
        await super().outcome(call, execution)


@dataclass
class Run:
    """One admitted, claimed run with its tool endpoint and its sandbox."""

    name: str
    claim: Claim
    effects: CountingEffectLog = field(default_factory=CountingEffectLog)
    executed: list[str] = field(default_factory=list[str])
    model_placeholder: str = field(default_factory=lambda: f"pxh-model-{uuid.uuid4().hex}")
    tools_placeholder: str = field(default_factory=lambda: f"pxh-tools-{uuid.uuid4().hex}")
    upstream: str = ""
    sandbox: SandboxHandle | None = None
    engine: ProcessHandle | None = None
    lines: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    readers: list[asyncio.Task[None]] = field(default_factory=list[asyncio.Task[None]])

    @property
    def binding(self) -> RunAttemptBinding:
        # The digests are computed here from what was pinned, independently of Node (which signs its own).
        return RunAttemptBinding(
            tenant=self.claim.org_id,
            scope=self.claim.session_id,
            run_id=self.claim.run_id,
            parent_run_id=None,
            attempt_id=self.claim.attempt_id,
            fence=self.claim.fence,
            tool_ids_digest=grant_digest([TOOL_ID]),
            routes_digest=route_digest((ROUTE,)),
        )

    def endpoint(self) -> RunToolEndpoint:
        async def record_note(call: ToolCall, ctx: ToolContext) -> ToolExecution:
            # The connector's credential stays in the worker: the call uses it, the answer never carries it.
            assert CONNECTOR_SECRET.startswith("ghp_")
            self.executed.append(json.dumps(call.arguments, sort_keys=True))
            return ToolExecution(status="ok", content=f"noted {len(self.executed)}")

        async def delete_everything(call: ToolCall, ctx: ToolContext) -> ToolExecution:
            self.executed.append("UNGRANTED")
            return ToolExecution(status="ok", content="deleted")

        def tool(name: str, tool_id: str, run: Any) -> CatalogTool:  # noqa: ANN401 - a ToolFn
            return CatalogTool(
                ToolSchema(name=name, description=f"{name} (C-13)", input_schema={"type": "object"}),
                ToolEffect(tool_id, "write", idempotent=False, sink=SinkDescriptor(egress="external", args_leave_boundary=True)),
                run,
            )

        catalog = InMemoryToolCatalog(
            [tool(TOOL, TOOL_ID, record_note), tool(UNGRANTED, UNGRANTED_ID, delete_everything)], [TOOL_ID]
        )
        binding = self.binding
        return RunToolEndpoint(
            binding=binding,
            catalog=catalog,
            effects=EffectRunner(self.effects),
            scope=InvocationScope(binding.run_id, binding.tenant, binding.attempt_id),
            ids=RandomIds(),
            citations=InMemoryCitationSink(),
        )

    def step(self, name: str) -> list[dict[str, Any]]:
        return [line for line in self.lines if line["step"] == name]

    async def read_engine(self) -> None:
        assert self.engine is not None
        buffer = b""
        async for chunk in self.engine.stdout:
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                if raw.strip():
                    line = json.loads(raw)
                    self.lines.append(line)
                    if line["step"] == "ready":
                        self.ready.set()

    async def drain(self) -> None:
        assert self.engine is not None
        async for _ in self.engine.stderr:
            pass


@dataclass
class Observed:
    """What every party outside the sandbox saw."""

    tools: RecordingGateway
    model: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    canary: list[str] = field(default_factory=list[str])


def _spec() -> dict[str, Any]:
    spec = dump_contract(minimal_run_spec())
    spec["model"]["policy"] = [{"provider": ROUTE.provider, "model": ROUTE.model}]
    spec["grants"]["tools"] = [{"toolId": TOOL_ID, "descriptorDigest": "sha256:" + "0" * 64}]
    return spec


async def _admit(node: _NodeServer, journal: NodeJournal, name: str) -> Run:
    run_id, session_id = f"run-c13-{name}", f"ses-c13-{name}"
    body = {"runId": run_id, "sessionId": session_id, "inputText": "c13", "spec": _spec()}
    _ = node.control.post("/admit", json=body).raise_for_status()
    claim = await journal.claim(run_id, worker_id=f"worker-c13-{name}", org_id=ORG)
    assert claim is not None
    return Run(name, claim)


async def _serve(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, host, port, reuse_address=True).start()
    return runner


@contextlib.asynccontextmanager
async def _outside(lane: Lane, keys: StaticGatewayKeySource, ledger: InMemoryBudgetLedger) -> AsyncIterator[tuple[Observed, FakeProvider, str]]:
    """E3, the fake provider and the canary; the tool gateway is the rig's (``stack.serving``)."""
    stack = lane.stack
    provider = FakeProvider()
    provider_runner = await _serve(provider.app(), "127.0.0.1", 0)
    provider_url = f"http://127.0.0.1:{provider_runner.addresses[0][1]}"
    resolver = StaticModelAccessResolver()
    transport = OpenAITransport(api_key=PROVIDER_KEY, model="gpt-4o", base_url=f"{provider_url}/v1")
    resolver.add(ORG, ROUTE, org_key_route(ROUTE, transport))
    grants = NodeRunModelGrants(base_url=lane.node.base_url)
    gateway = ModelGateway(
        verifier=GatewayTokenVerifier(keys, SystemClock()),
        grants=grants,
        resolver=resolver,
        budget=ledger,
        clock=SystemClock(),
        ids=RandomIds(),
    )
    observed = Observed(tools=stack.gateway)

    @web.middleware
    async def record(request: web.Request, handler: Any) -> web.StreamResponse:  # noqa: ANN401
        observed.model.append((request.path, request.headers.get("Authorization", "")))
        return await handler(request)

    model_app = create_model_gateway_app(gateway)
    model_app.middlewares.append(record)

    async def canary(request: web.Request) -> web.Response:
        observed.canary.append(f"{request.method} {request.path}")
        return web.json_response({"canary": True})

    canary_app = web.Application()
    canary_app.router.add_route("*", "/{tail:.*}", canary)
    runners = [
        provider_runner,
        await _serve(model_app, stack.host_ip, lane.e3_port),
        await _serve(canary_app, stack.host_ip, lane.canary_port),
    ]
    try:
        yield observed, provider, provider_url
    finally:
        for runner in runners:
            await runner.cleanup()


def _sandbox_spec(run: Run, tools_upstream: str, mode: RuntimeMode) -> SandboxSpec:
    claim = run.claim
    return SandboxSpec(
        image_digest=SANDBOX_IMAGE,
        mode=mode,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        egress=EgressPolicy(
            destinations=(
                EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),
                EgressDestination("tool-gateway", (EGRESS_PROXY_PORT,), upstream=tools_upstream),
            ),
            token_routes=(
                TokenRoute(run.model_placeholder, "model-gateway", "gateway:model"),
                TokenRoute(run.tools_placeholder, "tool-gateway", "gateway:tools"),
            ),
        ),
        mounts=(MountSpec(WORK),),
        labels={
            **OWNER,
            "pipeshub.agent": "C-13",
            "orgId": claim.org_id,
            "sessionId": claim.session_id,
            "runId": claim.run_id,
            "attemptId": claim.attempt_id,
        },
    )


async def _acquire(broker: DockerSandboxBroker, run: Run, tools_upstream: str, mode: RuntimeMode) -> None:
    claim = run.claim
    spec = _sandbox_spec(run, tools_upstream, mode)
    run.sandbox = await broker.acquire(spec, lease=LeaseRef("worker-c13", claim.fence), idempotency_key=claim.attempt_id)
    scripts = {name: (SCRIPTS / name).read_bytes() for name in ("fake_engine.py", "dump.py")}
    await run.sandbox.put_tar(WORK, iter_bytes(tar_of(scripts)))


async def _start_engine(run: Run, env: Mapping[str, str] | None = None) -> None:
    assert run.sandbox is not None
    run.engine = await run.sandbox.exec(
        ["python3", f"{WORK}/fake_engine.py"],
        env={
            "PXH_MODEL_URL": MODEL_URL,
            "PXH_TOOLS_URL": TOOLS_URL,
            "PXH_PROXY_URL": PROXY_URL,
            "PXH_MODEL_KEY": run.model_placeholder,
            "PXH_TOOLS_KEY": run.tools_placeholder,
            **(env or {}),
        },
        stdin=True,
    )
    run.readers = [asyncio.ensure_future(run.read_engine()), asyncio.ensure_future(run.drain())]
    await asyncio.wait_for(run.ready.wait(), 120)


async def _finish_engine(run: Run, orders: Mapping[str, Any] | None) -> int:
    assert run.engine is not None
    await run.engine.write((json.dumps(orders) if orders is not None else "").encode() + b"\n")
    await run.engine.close_stdin()
    code = await run.engine.wait(timeout_s=120)
    await asyncio.gather(*run.readers)
    return code


async def _dump(run: Run) -> dict[str, Any]:
    assert run.sandbox is not None
    process = await run.sandbox.exec(["python3", f"{WORK}/dump.py"], env={})
    out = bytearray()

    async def take() -> None:
        async for chunk in process.stdout:
            out.extend(chunk)

    async def drop() -> None:
        async for _ in process.stderr:
            pass

    await asyncio.wait_for(asyncio.gather(take(), drop()), 120)
    assert await process.wait(timeout_s=30) == 0
    return json.loads(bytes(out))


def _assert_no_secret(where: str, dump: Mapping[str, Any], secrets: tuple[str, ...]) -> None:
    """C13-15: P2.4a's helper (actual values and every forbidden credential shape), then JWT shapes."""
    texts = {**dump["files"], **dump["processes"]}
    for path, text in texts.items():
        _assert_clean(f"{where} {path}", [text], secrets)
        assert JWT.search(text) is None, f"{where} {path} carries a JWT-shaped string"


def _refusals(stack: RealStack, sandbox_id: str) -> list[str]:
    return [
        match.group(1)
        for line in stack.proxy_logs().splitlines()
        if "egress.refused" in line and f"sandboxId={sandbox_id}" in line
        for match in [re.search(r"reason=([a-z_]+)", line)]
        if match is not None
    ]


async def _claims_of(verifier: GatewayTokenVerifier, authorization: str) -> RunAttemptBinding:
    scheme, _, token = authorization.partition(" ")
    assert scheme == "Bearer"
    claims = await verifier.verify(token)
    assert isinstance(claims.binding, RunAttemptBinding)
    return claims.binding


def _assert_engine_saw_the_grant_and_the_model(run: Run) -> None:
    """C13-1..C13-4, C13-7 as the engine saw them; C13-2 as the tool and its effect log saw it."""
    (listed,) = run.step("tools/list")
    assert listed == {"step": "tools/list", "status": 200, "tools": [TOOL]}, run.lines
    first, second, ungranted = run.step("tools/call")
    assert (first["status"], first["isError"], first["disposition"]) == (200, False, "executed"), first
    assert (second["isError"], second["disposition"], second["text"]) == (False, "recorded", first["text"]), second
    assert (ungranted["isError"], ungranted["reasonCode"]) == (True, "not_granted"), ungranted
    (chat,) = run.step("chat")
    assert (chat["status"], chat["content"], chat["usage"]["prompt_tokens"]) == (200, "Writing files.", 120), chat
    (responses,) = run.step("responses")
    assert (responses["status"], responses["content"]) == (200, "Writing files."), responses
    assert run.executed == [json.dumps({"note": "c13"})]
    assert run.effects.records == [("intent", TOOL_ID), ("outcome", TOOL_ID)]


async def _assert_node_tokens_only(
    verifier: GatewayTokenVerifier, observed: Observed, runs: Mapping[str, Run]
) -> list[str]:
    """C13-6, C13-16: every request each gateway received carried a token Node issued for the sandbox's own
    run attempt (never a placeholder). Returns those tokens."""
    placeholders = {p for run in runs.values() for p in (run.model_placeholder, run.tools_placeholder)}
    authorizations = [auth for _, auth in observed.model] + observed.tools.authorizations
    assert len(observed.model) == 4 and len(observed.tools.authorizations) == 12, observed
    for authorization in authorizations:
        assert not any(p in authorization for p in placeholders), "a gateway received a placeholder"
        bound = await _claims_of(verifier, authorization)
        assert bound == runs[bound.run_id].binding
    return sorted({auth.partition(" ")[2] for auth in authorizations})


def _orders(lane: Lane, a: Run, b: Run, b_token: str, provider_url: str) -> dict[str, Any]:
    stack = lane.stack
    return {
        "foreignToken": b_token,
        "foreignToolsPlaceholder": b.tools_placeholder,
        "offAllowlist": [
            f"http://harness-state:3000/api/v1/agent-harness/internal/runs/{a.claim.run_id}/gateway-token",
            f"http://{stack.host_ip}:{lane.canary_port}/exfiltrate",
        ],
        "direct": [
            [stack.host_ip, lane.e3_port],
            [stack.host_ip, stack.gateway_port],
            [stack.host_ip, int(lane.node.base_url.rsplit(":", 1)[1])],
            [stack.host_ip, lane.canary_port],
            [stack.host_ip, stack.model_port],
            ["127.0.0.1", int(provider_url.rsplit(":", 1)[1])],
            ["169.254.169.254", 80],
            ["1.1.1.1", 80],
        ],
    }


async def _assert_gateways_refuse_on_their_own(lane: Lane, a: Run, b_token: str) -> None:
    """C13-14: what the proxy never lets through, each gateway refuses by itself (a K8s pod shares the netns)."""
    accept = {"Accept": "application/json, text/event-stream"}
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    chat = {"model": "engine-model", "messages": [{"role": "user", "content": "x"}]}
    e3 = f"http://{lane.stack.host_ip}:{lane.e3_port}/v1/chat/completions"
    async with loopback_client() as client:
        for bearer, reason in ((b_token, "binding_mismatch"), (a.tools_placeholder, "token_malformed")):
            headers = {**accept, "Authorization": f"Bearer {bearer}"}
            refused = await client.post(f"{a.upstream}/mcp", json=listing, headers=headers)
            assert (refused.status_code, refused.headers.get("x-gateway-refusal")) == (401, reason)
        for headers, reason in (({}, "token_missing"), ({"Authorization": f"Bearer {a.model_placeholder}"}, "token_malformed")):
            refused = await client.post(e3, json=chat, headers=headers)
            assert (refused.status_code, refused.headers.get("x-gateway-refusal")) == (401, reason)


@pytest.mark.parametrize("mode", [RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR], ids=["runc", "gvisor"])
async def test_c13_a_sandbox_reaches_tools_and_models_only_through_the_gateways_and_holds_no_secret(
    lane: Lane, mode: RuntimeMode
) -> None:
    node, stack = lane.node, lane.stack
    _ = node.control.post("/reset").raise_for_status()
    # Node's world clock drives its leases; bring it to wall time so its tokens and the gateways' clocks agree.
    now_ms = int(node.control.get("/now").json()["now"])
    _ = node.control.post("/advance", json={"seconds": (time.time() * 1000 - now_ms) / 1000}).raise_for_status()
    published = parse_published(node.control.get("/gateway-public-keys").json())
    assert published is not None and published.keys
    keys = StaticGatewayKeySource(published)
    verifier = GatewayTokenVerifier(keys, SystemClock())
    stack.gateway = RecordingGateway(ToolGatewayApp(GatewayTokenVerifier(keys, SystemClock())))

    journal = NodeJournal(base_url=node.base_url, credentials=_Credential(node.credential), lease_ms=60_000)
    a, b = await _admit(node, journal, "a"), await _admit(node, journal, "b")
    runs = {run.claim.run_id: run for run in (a, b)}
    ledger = InMemoryBudgetLedger(SystemClock())
    for run in (a, b):
        ledger.pin(RunAccountRef(ORG, run.claim.run_id), PinnedBudget(Cap(usd_micros=5_000_000, tokens=1_000_000)))
    namespace = f"io.pipeshub.c13.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-c13-" + namespace.rsplit(".", 1)[1]
    broker, provider = await engine_broker(stack, namespace, prefix, image=SANDBOX_IMAGE)
    if mode not in broker.modes():
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        skip_without_runsc()
    try:
        async with stack.serving(), _outside(lane, keys, ledger) as (observed, fake_provider, provider_url):
            for run in (b, a):
                run.upstream = await stack.mount(run.endpoint(), run.binding)
            # P5.9a: with the lane's pool on, both runs are bound to warm sandboxes.
            _ = await prime_pool(broker, _sandbox_spec(b, b.upstream, mode))
            warm = broker.warm_ids()
            for run in (b, a):
                await _acquire(broker, run, run.upstream, mode)
            assert a.sandbox is not None and b.sandbox is not None
            assert not broker.pool.enabled or {a.sandbox.id, b.sandbox.id} <= warm
            await _start_engine(b)
            await _start_engine(a)
            for run in (a, b):
                _assert_engine_saw_the_grant_and_the_model(run)
            tokens = await _assert_node_tokens_only(verifier, observed, runs)

            # C13-15 while both engines are alive, before A is handed anything hostile.
            secrets = (*tokens, node.credential, PROVIDER_KEY, CONNECTOR_SECRET, stack.control_token)
            for run in (a, b):
                dump = await _dump(run)
                assert dump["unreadable"] == {}, dump["unreadable"]
                _assert_no_secret(f"sandbox {run.name} (engine alive)", dump, secrets)
                # The scan sees what is there: the engine's own placeholders, in its environment.
                assert any(run.tools_placeholder in text for text in dump["processes"].values())

            b_tokens = [t for t in tokens if (await verifier.verify(t)).binding == b.binding]
            seen = (len(observed.model), len(observed.tools.authorizations), len(fake_provider.seen))
            assert await _finish_engine(a, _orders(lane, a, b, b_tokens[0], provider_url)) == 0
            for run in (a, b):
                _assert_no_secret(f"sandbox {run.name} (after the probes)", await _dump(run), secrets)
            assert await _finish_engine(b, None) == 0
            # No probe reached a gateway or the provider.
            assert (len(observed.model), len(observed.tools.authorizations), len(fake_provider.seen)) == seen

            # C13-17: the proxy refused exactly A's probes, once each, and nothing of B's.
            for step, reason in EXPECTED_REFUSALS.items():
                (probe,) = a.step(step)
                assert probe["refusal"] == reason, probe
            assert sorted(_refusals(stack, a.sandbox.id)) == sorted(EXPECTED_REFUSALS.values())
            assert _refusals(stack, b.sandbox.id) == []
            (direct,) = a.step("direct")
            assert all(result != "open" for result in direct["results"].values()), direct

            await _assert_gateways_refuse_on_their_own(lane, a, b_tokens[0])

            # C13-5: both model calls of each run are charged to that run, and only to it. Each call is 120 prompt
            # tokens, 100 of them read from the cache, and 30 out; cache reads weigh less on the token cap
            # (budget_tokens, 5848b39521).
            per_call = budget_tokens(TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100), ROUTE.model)
            assert 30 < per_call < 150
            for run in (a, b):
                left = await ledger.remaining(RunAccountRef(ORG, run.claim.run_id))
                spent, held = left.committed, left.reserved
                assert (spent.tokens, spent.usd_micros > 0, held.tokens) == (2 * per_call, True, 0), left

            # C13-3: the provider got E3's four calls with the org key, nothing of a sandbox's.
            sandbox_values = {*tokens, *(p for run in (a, b) for p in (run.model_placeholder, run.tools_placeholder))}
            assert len(fake_provider.seen) == 4
            for _, headers, body in fake_provider.seen:
                assert headers["authorization"] == f"Bearer {PROVIDER_KEY}"
                assert not any(value in json.dumps(headers) + json.dumps(body) for value in sandbox_values)

            # C13-6, C13-18: Node issued the tokens to the proxy; nothing outside the gateways was reached.
            requests = node.control.get("/requests").json()["requests"]
            issued = {r.split("/")[-2] for r in requests if r.startswith("POST ") and r.endswith("/gateway-token")}
            assert issued == {a.claim.run_id, b.claim.run_id}
            allowed = re.compile(
                r"^(POST /api/v1/agent-harness/internal/runs/[^/]+/(claim|gateway-token)"
                r"|GET /api/v1/agent-harness/internal/runs/[^/]+/model-grant)$"
            )
            assert [r for r in requests if not allowed.match(r)] == []
            assert observed.canary == []
            assert stack.models.paths == [] and stack.models.issued == []
            assert all(not calls for calls in stack.models.authorizations.values())
    finally:
        for run in (a, b):
            if run.sandbox is not None:
                with contextlib.suppress(Exception):
                    await broker.release(run.sandbox)
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()

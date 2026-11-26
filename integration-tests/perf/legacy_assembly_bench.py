"""Legacy path, query service: what the resolved-turn header saves before the first streamed byte (CPD-10 LEG-11a).

Drives the production ``POST /agent/{id}/chat/stream`` handler in process, one ASGI call per request (no network,
no server), for an agent with three toolsets and one MCP server, in two arms:

* ``reads``: no header, today's per-request reads;
* ``header``: a valid ``X-PipesHub-Resolved`` naming access and a binding for each toolset and the MCP server.

The graph and configuration stores are counting in-memory fakes with a fixed delay per read (``--delays-ms``).
The model is instant. The loop is a stand-in that runs the loop's own policy steps before its first frame with
the production code (``build_initial_state``, ``AgentContext.from_chat_state``, the factory's two switch reads,
``register_legacy_flow_guard``), then streams one frame: tool loading, the skill index and the MCP session are the
same in both arms and are not in the measurement.

Per arm and delay it reports the reads by kind before the first streamed byte, the wall time to that byte (p50,
p95) and the process CPU time per request (``time.process_time``), over ``--samples`` requests after ``--warmups``.
The arms are interleaved (A B B A) so both see the same host load. No threshold is asserted: absolute numbers are
taken on a quiet host (FINAL-VERIFICATION stage 12).

    cd backend/python && PYTHONPATH=$PWD python ../../integration-tests/perf/legacy_assembly_bench.py \\
        --samples 200 --delays-ms 0 1 --output /path/to/legacy-assembly.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

_PYTHON_ROOT = Path(__file__).resolve().parents[2] / "backend" / "python"
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ARMS = ("reads", "header")
AGENT = "bench-agent"
CALLER = "bob"
CONVERSATION = "conv-bench"
TOOLSETS = ("inst-1", "inst-2", "inst-3")
MCP = "mcp-1"
FIRST_FRAME = 'event: STATE_SNAPSHOT\ndata: {"type": "STATE_SNAPSHOT"}\n\n'


def _kind(key: str) -> str:
    """What a configuration read is, for the table."""
    kinds = {
        "/services/agentHarness/gatewayPublicKeys": "signing keys",
        "/services/agentHarness/approvals": "approvals document",
        "/services/platform/settings": "Labs settings",
        "/services/toolset-instances": "toolset instances",
    }
    if key in kinds:
        return kinds[key]
    for prefix, kind in (
        ("/services/toolsets/", "toolset credential"),
        ("/services/mcp/credentials/", "MCP credential"),
        ("/services/mcp/instances/", "MCP instance"),
    ):
        if key.startswith(prefix):
            return kind
    return f"config {key.rsplit('/', 1)[-1]}"


class Reads:
    """Every store read of the request in flight, by kind, with the same delay before each."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.counts: Counter[str] = Counter()

    async def read(self, kind: str) -> None:
        self.counts[kind] += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)


def _stores(reads: Reads) -> tuple[Any, Any]:
    """The graph and the configuration store of ``tests/support`` (the fakes the route tests use), counted."""
    from tests.support.agent_routes import AGENTS, InMemoryGraph
    from tests.support.config_service import FakeConfigurationService
    from tests.unit.harness.credentials.support import owned_toolsets
    from tests.unit.harness.legacy.support import ACTIVE, KEYS_NODE, keys_document

    class Graph(InMemoryGraph):
        def __getattribute__(self, name: str) -> Any:  # noqa: ANN401
            attribute = super().__getattribute__(name)
            if name.startswith(("_", "add_", "edges_", "calls_to", "fail")) or not asyncio.iscoroutinefunction(attribute):
                return attribute

            async def counted(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
                await reads.read(f"graph {name}")
                return await attribute(*args, **kwargs)

            return counted

    class Config(FakeConfigurationService):
        async def get_config(self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
            await reads.read(_kind(key))
            return await super().get_config(key, default, use_cache, raise_on_error=raise_on_error)

    graph = Graph()
    graph.add_agent(AGENT, "alice", share_with_org=True, systemPrompt="be brief")
    for instance in TOOLSETS:
        graph.add_node("agentToolsets", {"_key": instance, "name": "jira", "displayName": instance, "instanceId": instance})
        graph.add_node("agentTools", {"_key": f"{instance}-t", "name": "search", "fullName": f"{instance}.search"})
        graph.add_edge("toolsetHasTool", {"_from": f"agentToolsets/{instance}", "_to": f"agentTools/{instance}-t"})
        graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/{AGENT}", "_to": f"agentToolsets/{instance}"})
    graph.add_node("agentMcpServers", {"_key": "m1", "instanceId": MCP, "displayName": "Notes", "name": "notes"})
    graph.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/{AGENT}", "_to": "agentMcpServers/m1"})
    nodes: dict[str, Any] = owned_toolsets({f"/services/toolsets/{i}/u-bob": {"isAuthenticated": True} for i in TOOLSETS})
    nodes[f"/services/mcp/instances/{MCP}"] = {
        "_id": MCP, "orgId": "org-1", "createdBy": "u-alice", "instanceName": "Notes", "serverType": "notes",
        "transport": "streamable_http", "url": "https://mcp.example.test/mcp", "authMode": "api_token",
    }
    nodes[f"/services/mcp/credentials/{MCP}/u-bob"] = {"isAuthenticated": True}
    nodes[KEYS_NODE] = keys_document(ACTIVE)
    nodes["/services/agentHarness/approvals"] = {"orgs": {"org-1": {"flow": {"level": "ask", "above": "private"}}}}
    return graph, Config(nodes, flags={"ENABLE_MCP": True})


async def _loop_stand_in(
    query_info: dict[str, Any], user_info: dict[str, Any], llm: Any, logger: Any, retrieval_service: Any,  # noqa: ANN401
    graph_provider: Any, reranker_service: Any, config_service: Any, org_info: Any, **kwargs: Any,  # noqa: ANN401
) -> AsyncIterator[str]:
    """The loop's policy steps before its first frame, by the production functions, then one frame."""
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.flow_guard import register_legacy_flow_guard
    from app.agents.mcp.service import is_mcp_enabled
    from app.modules.agents.qna.chat_state import build_initial_state
    from app.services.featureflag.platform_settings import is_skills_enabled
    from harness_core.kernel.hooks.registry import HookRegistry

    state = build_initial_state(
        query_info, user_info, llm, logger, retrieval_service, graph_provider, reranker_service, config_service,
        kwargs.get("model_name") or "", kwargs.get("model_key") or "", org_info, has_sql_connector=False,
    )
    context = AgentContext.from_chat_state(state)
    # app/agents/agent_loop/factory.py reads these two switches before it builds the loop.
    if context.mcp_servers:
        _ = await is_mcp_enabled(config_service)
    _ = await is_skills_enabled(config_service)
    _ = await register_legacy_flow_guard(HookRegistry(), context)
    yield FIRST_FRAME


def _app(graph: Any, config: Any) -> Any:  # noqa: ANN401
    """The production agent router; the model and the loop are replaced at the route's edge."""
    from fastapi import FastAPI

    import app.api.routes.agent as route
    from tests.support.agent_routes import USERS, FakeContainer, _TestAuth

    async def instant_model(*_args: object, **_kwargs: object) -> tuple[object, dict[str, Any], dict[str, Any]]:
        return object(), {"isMultimodal": False, "provider": "openai"}, {}

    async def no_entity_store(*_args: object, **_kwargs: object) -> None:
        return None

    route.get_llm_for_chat = instant_model  # type: ignore[assignment]
    route.load_entity_vector_store = no_entity_store  # type: ignore[assignment]
    route.run_agent_loop_stream = _loop_stand_in  # type: ignore[assignment]
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1/agent")
    app.container = FakeContainer(graph, config)  # type: ignore[attr-defined]
    app.add_middleware(_TestAuth, users=USERS)
    return app


def _header() -> str:
    """What Node signs for this turn: access, every Labs stage, the org's flow rule, a binding per installation."""
    from tests.unit.harness.legacy.support import VECTOR, claims, sign

    now = int(time.time())
    owner = "user:u-bob"
    return sign(
        claims(
            now,
            org="org-1", sub="u-bob", agent=AGENT, cid=CONVERSATION,
            pin={**VECTOR["valid"]["payload"]["pin"], "scope": {"agentKey": AGENT, "projectId": None}},
            stages={"ENABLE_ACTIONS": True, "ENABLE_MCP": True, "ENABLE_SKILLS": True, "ENABLE_USER_CONTEXT": True},
            flow=[{"level": "ask", "above": "private"}],
            access={"run": True},
            bindings=[{"installation": i, "owner": owner, "ref": f"bnd-{i}"} for i in (*TOOLSETS, MCP)],
        )
    )


@dataclass
class Sample:
    first_byte_ms: float
    cpu_ms: float
    reads: dict[str, int]


async def _request(app: Any, reads: Reads, header: str | None) -> Sample:  # noqa: ANN401
    """One request as an ASGI call; the clock stops at the first body byte, the CPU clock at the last."""
    body = json.dumps({"query": "what changed?", "conversationId": CONVERSATION}).encode()
    headers = [(b"content-type", b"application/json"), (b"x-test-user", CALLER.encode())]
    if header is not None:
        headers.append((b"x-pipeshub-resolved", header.encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": f"/api/v1/agent/{AGENT}/chat/stream", "raw_path": b"", "query_string": b"",
        "headers": headers, "client": ("127.0.0.1", 1), "server": ("query", 80), "root_path": "",
    }
    sent = False
    done = asyncio.Event()
    first: dict[str, Any] = {}

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            first["status"] = message["status"]
        elif message["type"] == "http.response.body":
            if message.get("body") and "at" not in first:
                first["at"] = time.perf_counter()
                first["reads"] = dict(reads.counts)
                first["frame"] = bytes(message["body"])
            if not message.get("more_body"):
                done.set()

    reads.counts.clear()
    cpu = time.process_time()
    start = time.perf_counter()
    await app(scope, receive, send)
    cpu_ms = (time.process_time() - cpu) * 1000
    if first.get("status") != 200 or first.get("frame") != FIRST_FRAME.encode():
        raise RuntimeError(f"the request was not served: {first.get('status')} {first.get('frame')!r}")
    return Sample((first["at"] - start) * 1000, cpu_ms, first["reads"])


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


@dataclass
class ArmResult:
    arm: str
    delay_ms: float
    samples: int
    first_byte_p50_ms: float
    first_byte_p95_ms: float
    cpu_mean_ms: float
    cpu_p50_ms: float
    reads_total: int
    reads: dict[str, int] = field(default_factory=dict)
    reads_varied: bool = False


async def _run(delay_ms: float, samples: int, warmups: int) -> list[ArmResult]:
    reads = Reads(delay_ms / 1000)
    graph, config = _stores(reads)
    app = _app(graph, config)

    async def one(arm: str) -> Sample:
        return await _request(app, reads, _header() if arm == "header" else None)

    for _ in range(warmups):
        for arm in ARMS:
            _ = await one(arm)
    taken: dict[str, list[Sample]] = {arm: [] for arm in ARMS}
    order = ("reads", "header", "header", "reads")
    for index in range(samples * 2):
        arm = order[index % 4]
        taken[arm].append(await one(arm))
    results = []
    for arm in ARMS:
        rows = taken[arm]
        wall, cpu = [s.first_byte_ms for s in rows], [s.cpu_ms for s in rows]
        shapes = Counter(json.dumps(s.reads, sort_keys=True) for s in rows)
        usual = json.loads(shapes.most_common(1)[0][0])
        results.append(
            ArmResult(
                arm, delay_ms, len(rows), _percentile(wall, 0.5), _percentile(wall, 0.95), statistics.fmean(cpu),
                _percentile(cpu, 0.5), sum(usual.values()), usual, reads_varied=len(shapes) > 1,
            )
        )
    return results


def _table(results: list[ArmResult]) -> str:
    lines = [
        "| delay/read | arm | n | reads before first byte | first byte p50 ms | p95 ms | CPU mean ms | CPU p50 ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.delay_ms:g} ms | {r.arm} | {r.samples} | {r.reads_total} | {r.first_byte_p50_ms:.2f} | "
        f"{r.first_byte_p95_ms:.2f} | {r.cpu_mean_ms:.2f} | {r.cpu_p50_ms:.2f} |"
        for r in results
    ]
    kinds = sorted({kind for r in results for kind in r.reads})
    by_arm = {r.arm: r for r in results if r.delay_ms == results[0].delay_ms}
    lines += ["", "| read before the first byte | " + " | ".join(by_arm) + " |", "|---|" + "---|" * len(by_arm)]
    lines += [f"| {kind} | " + " | ".join(str(by_arm[arm].reads.get(kind, 0)) for arm in by_arm) + " |" for kind in kinds]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=int, default=200, help="requests per arm, per delay, after the warm-ups")
    parser.add_argument("--warmups", type=int, default=20)
    parser.add_argument("--delays-ms", type=float, nargs="+", default=[0.0, 1.0])
    parser.add_argument("--output", type=Path, default=None, help="where to write the JSON report")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    os.environ.pop("CONTROL_PLANE_POLICY_STORE", None)
    load_start = os.getloadavg()[0]
    results: list[ArmResult] = []
    for delay in args.delays_ms:
        results += asyncio.run(_run(delay, args.samples, args.warmups))
    load_end = os.getloadavg()[0]
    report = {
        "bench": "legacy_assembly",
        "samples": args.samples,
        "warmups": args.warmups,
        "interleaving": "reads header header reads",
        "cpus": os.cpu_count(),
        "loadavg_1m_start": load_start,
        "loadavg_1m_end": load_end,
        "results": [asdict(r) for r in results],
    }
    print(_table(results))
    print(f"\nhost: {os.cpu_count()} CPUs, 1-minute load average {load_start:.2f} at start, {load_end:.2f} at end")
    if any(r.reads_varied for r in results):
        print("note: the reads before the first byte were not the same for every sample of an arm (the usual shape is shown)")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

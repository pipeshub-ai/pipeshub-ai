"""PERFA-4, PERFA-7 on Node (WRK-35, WRK-37; QA22-F1): the probe's cases against Node's own commit route.

The production kernel and journal client drive a real Node (its internal router, validation,
ExecutionService and journal) over the in-memory store, and over the Mongo adapters when ``MONGO_IT_URI``
names a replica set. Before PERF-A, 51 parallel reads failed here with ``VALIDATION_ERROR`` and a run
with more than 100 open calls could not be ended.
"""

from __future__ import annotations

import shutil
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.journal_node import NodeJournal
from harness_core.contracts import dump_contract
from harness_core.effects.types import allow_all
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.types import RunNotClaimable
from harness_core.kernel.durable import DurableKernel, ToolExecution
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import FunctionToolPort, ScriptedModelPort
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness.engines.p9_lane import MongoNode, mongo_uri, object_session
from tests.unit.harness.test_node_journal_contract import (
    LEASE_MS,
    NODE_APP,
    _Credential,  # pyright: ignore[reportPrivateUsage]
    _NodeServer,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping
    from pathlib import Path

    import httpx
    from pydantic import JsonValue

    from app.harness.journal_node import NodeClaim
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.durable import (
        DeltaKind,
        Disposition,
        ModelCall,
        SampledTurn,
        ToolContext,
    )

PROBE_CASES = [50, 51, 100, 101, 500]


class Crash(BaseException):
    """A simulated process death: no kernel handler runs."""


@dataclass
class Node:
    base_url: str
    credential: str
    control: httpx.Client
    org: str
    session_for: Callable[[str], str]
    # The in-memory server's clock is the test's; Mongo's is the database's.
    moves_time: bool


@pytest.fixture(scope="module", params=["memory", "mongo"])
def node(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Node]:
    if not (NODE_APP / "node_modules").is_dir() or shutil.which("npx") is None:
        pytest.skip("the Node workspace is not installed (npm ci)")
    if request.param == "memory":
        server = _NodeServer()
        _ = server.control.post("/reset").raise_for_status()
        yield Node(server.base_url, server.credential, server.control, "org-contract", lambda run: f"ses-{run}", True)
        server.control.close()
        server.stop()
        return
    uri = mongo_uri()
    if uri is None:
        pytest.skip("needs MONGO_IT_URI (a MongoDB replica set) for Node's Mongo mode")
    root: Path = tmp_path_factory.mktemp("perfa-node")
    mongo = MongoNode("127.0.0.1", uri, f"pxh_perfa_{uuid.uuid4().hex[:10]}", root / "state", root / "node.log")
    try:
        yield Node(mongo.base_url, mongo.credential, mongo.control, "66f6a6a6a6a6a6a6a6a6a6a1", object_session, False)
    finally:
        mongo.stop()
        mongo.control.close()


class _Sampled:
    """The scripted model port, telling the stack when a response has been sampled."""

    def __init__(self, port: ScriptedModelPort, stack: Stack) -> None:
        self._port, self._stack = port, stack

    def __getattr__(self, name: str) -> object:
        return getattr(self._port, name)

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        sampled = await self._port.sample(call, on_delta)
        if self._stack.on_sampled is not None:
            self._stack.on_sampled()
        return sampled


class Stack:
    """One run on a Node: the production journal client and kernel, a scripted model, counted reads."""

    def __init__(self, node: Node, run_id: str, calls: int, tool: str = "lookup") -> None:
        self.node, self.run_id, self.calls = node, run_id, calls
        self.approval: ApprovalPolicy = allow_all
        self.journal = NodeJournal(base_url=node.base_url, credentials=_Credential(node.credential), lease_ms=LEASE_MS)
        self.ran: Counter[str] = Counter()
        self.crash_reads = False
        self.tools = FunctionToolPort()
        self.tools.add("lookup", self._lookup, tags=[("category", "read")])
        self.tools.add("post", self._lookup, tags=[("category", "write")])
        reads = Reply(tool_calls=[ToolCallSpec(name=tool, args={"q": f"q{i}"}) for i in range(calls)])
        script = ModelScript(id="perfa", turns=[Turn(reply=reads), Turn(reply=Reply(text="done"))])
        self.model = ScriptedModel(script)
        self.port = ScriptedModelPort(self.model)
        self.on_sampled: Callable[[], None] | None = None
        body = {
            "runId": run_id,
            "sessionId": node.session_for(run_id),
            "inputText": "read widely",
            "spec": dump_contract(minimal_run_spec()),
        }
        _ = node.control.post("/admit", json=body).raise_for_status()

    async def _lookup(self, args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        if self.crash_reads:
            raise Crash
        self.ran[str(args["q"])] += 1
        return ToolExecution(status="ok", content={"found": args["q"]})

    async def attempt(self) -> tuple[NodeClaim, Disposition]:
        claim = await self.journal.claim(self.run_id, worker_id="perfa", org_id=self.node.org)
        assert claim is not None
        kernel = DurableKernel(
            journal=self.journal,
            content=PipesHubContentStore(claim.content),
            model=_Sampled(self.port, self),  # type: ignore[arg-type]
            tools=self.tools,
            clock=ManualClock(),
            approval=self.approval,
        )
        return claim, await kernel.run_claimed(claim)

    async def crash(self) -> None:
        with pytest.raises(Crash):
            _ = await self.attempt()
        _ = self.node.control.post("/advance", json={"seconds": LEASE_MS / 1000 + 1}).raise_for_status()
        _ = self.node.control.post("/sweep").raise_for_status()

    async def events(self, claim: NodeClaim) -> list[JournalEvent]:
        return [e for e in await self.journal.read(claim.stream_id) if e.run_id == self.run_id]


def per_invocation(events: list[JournalEvent], event_type: str) -> Counter[str]:
    return Counter(str(e.data["invocationId"]) for e in events if e.type == event_type)


@pytest.mark.parametrize("calls", PROBE_CASES)
async def test_perfa_4_node_accepts_a_turn_of_parallel_reads_whatever_its_width(node: Node, calls: int) -> None:
    stack = Stack(node, f"run-wide-{calls}", calls)
    try:
        claim, done = await stack.attempt()
        events = await stack.events(claim)
    finally:
        await stack.journal.aclose()
    assert done.status == "completed", done
    assert events[-1].type == "run.completed"
    for event_type in ("tool.intent", "tool.outcome"):
        counts = per_invocation(events, event_type)
        assert len(counts) == calls and set(counts.values()) == {1}, event_type
    assert len(stack.ran) == calls and set(stack.ran.values()) == {1}
    assert [e.seq for e in events] == sorted({e.seq for e in events}), "one ordered stream, no event twice"
    assert {e.fence for e in events if e.type == "tool.outcome"} == {1}


@pytest.mark.parametrize("calls", [MAX_COMMIT_EVENTS, MAX_COMMIT_EVENTS + 1, 500])
async def test_perfa_7_node_accepts_the_end_of_a_run_stopped_with_that_many_open_calls(node: Node, calls: int) -> None:
    stack = Stack(node, f"run-stop-{calls}", calls)
    stack.on_sampled = lambda: node.control.post("/cancel", json={"runId": stack.run_id}).raise_for_status() and None
    try:
        claim, done = await stack.attempt()
        events = await stack.events(claim)
    finally:
        await stack.journal.aclose()
    assert done.status == "cancelled", done
    assert events[-1].type == "run.cancelled", "the user's stream gets its terminal event"
    assert len(per_invocation(events, "tool.outcome")) == min(calls, MAX_COMMIT_EVENTS)
    assert stack.ran == Counter(), "nothing runs after Stop"


@pytest.mark.parametrize("calls", [MAX_COMMIT_EVENTS + 1, 500])
async def test_perfa_7_node_quarantines_a_crash_looping_run_with_that_many_open_calls(node: Node, calls: int) -> None:
    """QA22-F1's endless loop, end to end: the fourth claim's quarantine is accepted and the run is over."""
    if not node.moves_time:
        pytest.skip("needs the server's clock moved past a lease (the in-memory mode)")
    stack = Stack(node, f"run-loop-{calls}", calls)
    stack.crash_reads = True
    try:
        for _ in range(3):
            await stack.crash()
        claim, done = await stack.attempt()
        events = await stack.events(claim)
        with pytest.raises(RunNotClaimable):
            _ = await stack.journal.claim(stack.run_id, worker_id="perfa", org_id=node.org)
    finally:
        await stack.journal.aclose()
    assert done.status == "quarantined", done
    assert events[-1].type == "run.quarantined" and events[-1].data == {"attempts": 4, "lastErrorType": "crash_loop"}


async def test_perfa_10_node_suspends_a_run_that_asks_about_more_writes_than_a_commit_holds(node: Node) -> None:
    calls = MAX_COMMIT_EVENTS + 30
    stack = Stack(node, "run-asks", calls, tool="post")
    stack.approval = lambda call, effect: "ask"
    try:
        claim, done = await stack.attempt()
        events = await stack.events(claim)
    finally:
        await stack.journal.aclose()
    assert done.status == "suspended", done
    asked = Counter(str(e.data["interactionId"]) for e in events if e.type == "interaction.requested")
    assert len(asked) == calls and set(asked.values()) == {1}
    assert events[-1].type == "run.suspended" and stack.ran == Counter(), "nothing is sent before a person decides"

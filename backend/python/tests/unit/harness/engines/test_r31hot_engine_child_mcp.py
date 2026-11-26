"""R31-HOT, ADR-OWD-07 D-LAZY-8 on a live Node: a code_task job of a parent that holds an MCP server whole keeps it.

The owner's default assistant holds Atlassian Rovo whole (``x.pipeshub.mcp[].tools: at_first_use``, D-LAZY-2), so its
tool grants name none of Rovo's tools. Node pinned each child's MCP servers from those grants alone, and every engine
job ran with ``mcpServers=0``. Here Node is real (``journal-http-server.ts``: the internal routes and
``ChildRunService``): a loop parent holding the world's MCP server whole hands work to an engine child, which this
process claims and runs as a worker does, with a scripted engine behind the run's tool gateway. The child's tool
environment lists the server, its first call lists it through the gateway, and the org's per-tool rules still decide
the call: ask holds it, deny refuses it, an explicit allow runs it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.constants.mcp_server_constants import get_mcp_user_tool_rules_path
from app.agents.mcp.models import MCPToolInfo
from app.harness.engines.durable import DurableEngineServices, PipesHubEngineAssembler
from harness_core.clock import SystemClock
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.durable.kernel import EngineKernel
from harness_core.engines.profiles import profile_json
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.ids import RandomIds
from harness_core.journal.children import (
    ChildExecutorRequest,
    ChildGrantRequest,
    ChildRequest,
)
from harness_core.sandbox.types import RuntimeMode
from harness_core.testing import ModelScript, Reply, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import (
    FAKE_VERSION,
    IMAGE,
    ScriptedEngineAdapter,
    engine_profile,
)
from harness_core.testing.engines.gateway import ConformanceGateway
from harness_core.testing.engines.tool_host import InMemoryToolGatewayHost
from harness_core.testing.sandbox.egress import FakeEgressProxy
from harness_core.testing.sandbox.fake import FakeSandboxBroker
from harness_core.testing.scripted_model import ScriptedModel
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from tests.unit.harness.assembly import world
from tests.unit.harness.test_node_journal_contract import NodeHarness, node_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver.types import Disposition

    from harness_core.journal.types import Claim, JournalEvent
    from tests.unit.harness.test_node_journal_contract import (
        _NodeServer,  # pyright: ignore[reportPrivateUsage]
    )

__all__ = ["node_server"]

NODE_ORG = "org-contract"
APPROVALS = "/services/agentHarness/approvals"
# The test Node's run actor: the person whose own MCP tool rules the job reads.
ACTOR = "actor-contract"
GET_ISSUE = f"mcp@{world.MCP}/get_issue"
CREATE_ISSUE = f"mcp@{world.MCP}/create_issue"
# The recorded shape: a read the server marks read-only, and a create it says nothing of (so a write).
TOOLS = [
    MCPToolInfo(
        name="get_issue", namespaced_name="mcp_github_get_issue", description="Read an issue.",
        input_schema={"type": "object", "properties": {}}, annotations={"readOnlyHint": True},
    ),
    MCPToolInfo(
        name="create_issue", namespaced_name="mcp_github_create_issue", description="Create an issue.",
        input_schema={"type": "object", "properties": {}},
    ),
]
# What the parent's own turn read when it asked: nothing private, so the flow guard has nothing to hold.
NOTHING_READ: dict[str, Any] = {"complete": True, "sources": []}
# The default assistant's grant: the server attached with no tool list, held whole.
WHOLE_AGENT = world.agent_doc(
    mcpServers=[{"instanceId": world.MCP, "name": "GitHubMCP", "displayName": "GitHub", "typeId": "github"}]
)


@dataclasses.dataclass
class _Ran:
    disposition: Disposition
    child: Claim
    events: list[JournalEvent]

    def of(self, event_type: str) -> list[JournalEvent]:
        return [e for e in self.events if e.type == event_type and e.run_id == self.child.run_id]


class _Lane:
    """A loop parent on the live Node that hands work to an engine child, which this process runs as a worker."""

    def __init__(self, server: _NodeServer) -> None:
        self._node = NodeHarness(server)
        self.services = world.Services(WHOLE_AGENT, org=NODE_ORG)
        clock = SystemClock()
        host = InMemoryToolGatewayHost()
        brain = ConformanceGateway(
            endpoints=EngineControllerConfig(images={}).endpoints("unused-0123456789", "unused-0123456789"), grant=[]
        )
        proxy = FakeEgressProxy(clock=clock, gateways={"model-gateway": brain._model})  # pyright: ignore[reportPrivateUsage]
        proxy.route_upstream(host.base, host.handle)
        self._broker = FakeSandboxBroker(
            clock=clock, ids=RandomIds(), modes=frozenset({RuntimeMode.DOCKER_HARDENED}), egress_proxy=proxy
        )
        engines = DurableEngineServices(
            broker=self._broker,
            tool_host=host,
            engines=AllowlistEngineRegistry([ScriptedEngineAdapter()], [FAKE_VERSION]),
            config=EngineControllerConfig(images={FAKE_VERSION: IMAGE}, tick_s=0.001),
            objects=InMemoryBinaryObjectStore(),
            route_for=self._node.node.attempt_route,
            clock=clock,
            placeholder_key=b"k" * 32,
        )
        script = ScriptedModel(ModelScript(id="r31hot", turns=[Turn(reply=Reply(text="ok"))]))
        self.assembler = PipesHubEngineAssembler(
            self.services, engines, model_factory=world.model_factory(ScriptedModelPort(script))
        )

    def _kernel(self) -> EngineKernel:
        return EngineKernel(
            journal=self._node.node,
            assembler=self.assembler,
            clock=SystemClock(),
            broker=self._broker,
            poll_s=0.005,
            flush_interval_s=0.005,
            approval_hold_s=0.0,
        )

    async def job(self, goal: str, *, inherited_label: dict[str, Any] | None = NOTHING_READ) -> Claim:
        """The parent's code_task child as ``CodeTaskDelegation`` asks for it (its workspace fork, no tools named: the
        job takes its parent's grant), claimed by a worker."""
        parent_id = f"run-{uuid.uuid4().hex[:8]}"
        spec = world.pinned_spec(
            tool_ids=[f"jira@{world.JIRA}/search_issues"], whole=[world.MCP], executor={"kind": "loop"}, max_children=2
        )
        raw = cast("dict[str, Any]", dump_contract(spec))
        # The route the test Node's access authority decides an engine child's access on (as LCFIX's turn).
        raw["model"]["policy"] = [{"provider": "openai", "model": "gpt-5"}]
        raw["extensions"]["x.pipeshub"]["mounts"] = ["/workspace"]
        raw["extensions"]["x.pipeshub"]["codeTask"] = {"profile": profile_json(engine_profile())}
        self._node.admit(parent_id, session_id=f"session-{parent_id}", spec=validate_contract(RunSpec, raw))
        parent = await self._node.journal.claim(parent_id, worker_id="w-loop")
        assert parent is not None
        admitted = await self._node.node.spawn(
            parent.token,
            ChildRequest(
                invocation_id=f"{parent_id}.code", goal=goal, grants=ChildGrantRequest(mounts=("/workspace",)),
                executor=ChildExecutorRequest(),
                inherited_label=inherited_label,  # type: ignore[arg-type]
            ),
        )
        child = await self._node.journal.claim(admitted.child_run_id, worker_id="w-engine")
        assert child is not None
        return child

    async def run(self, goal: str, **job: Any) -> _Ran:  # noqa: ANN401
        child = await self.job(goal, **job)
        disposition = await self._kernel().run_claimed(child, claim_sent_at=SystemClock().suspend_aware())
        return _Ran(disposition, child, await self._node.node.read(child.stream_id, after_seq=0))


@pytest.fixture
def listings() -> Iterator[list[str]]:
    with world.deployment(org=NODE_ORG), pytest.MonkeyPatch.context() as patch:
        # Main's per-tool rules on (#4003), as a deployment runs them: a write nobody set a rule for asks.
        patch.setenv("MCP_TOOL_APPROVALS", "true")
        patch.setitem(world.MCP_TOOLS, world.MCP, TOOLS)
        seen: list[str] = []
        real = MCPSessionManager.tools

        async def counted(self: MCPSessionManager, server: Any, namespace: str) -> Any:  # noqa: ANN401
            seen.append(server.instance_id)
            return await real(self, server, namespace)

        patch.setattr(MCPSessionManager, "tools", counted)
        yield seen


@pytest.fixture
def lane(node_server: _NodeServer, listings: list[str]) -> _Lane:
    del listings
    return _Lane(node_server)


def _call(tool: str) -> str:
    return f"tool {tool} {json.dumps({})}\nsay done"


def _deployment_rules(lane: _Lane, tools: dict[str, str]) -> None:
    lane.services.config.put(APPROVALS, {"default": {"tools": tools}})


def _own_rules(lane: _Lane, tools: dict[str, str]) -> None:
    """The person's own rules for the server's tools, as the approval card's "Always allow" saves them."""
    lane.services.config.put(get_mcp_user_tool_rules_path(ACTOR, world.MCP), {"tools": tools})


def _intents(ran: _Ran, tool_id: str) -> list[JournalEvent]:
    return [e for e in ran.of("tool.intent") if e.data.get("toolId") == tool_id]


def _held(ran: _Ran, tool_id: str) -> str:
    """The rule that holds the job's call of ``tool_id``: the run waits on a person, the call never ran."""
    assert ran.disposition.status == "suspended", ran.disposition
    assert not _intents(ran, tool_id), "a held call never starts"
    [reason] = ran.of("interaction.explained")
    assert reason.data["toolId"] == tool_id
    return str(reason.data["rule"])


async def test_r31hot_1_the_job_holds_the_server_whole_and_its_environment_lists_it(
    lane: _Lane, listings: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    child = await lane.job("say hello")
    [server] = (child.spec.extensions or {})["x.pipeshub"]["mcp"]
    assert (server["instanceId"], server.get("tools")) == (world.MCP, "at_first_use"), dump_contract(child.spec)
    assert not [t for t in child.spec.grants.tools if t.tool_id.startswith("mcp@")]
    caplog.set_level(logging.INFO)
    runtime = await lane.assembler.build(lane._kernel().attempt_of(child, engine_profile()))  # pyright: ignore[reportPrivateUsage]
    try:
        environments = [r.getMessage() for r in caplog.records if r.getMessage().startswith("run.tool_environment")]
        assert environments and "mcpServers=1" in environments[-1], environments
        assert listings == [], "a server held whole is listed at its first use, not when the job starts"
    finally:
        if runtime.close is not None:
            await runtime.close()


async def test_r31hot_2_the_jobs_first_call_lists_the_server_through_the_gateway_and_runs(
    lane: _Lane, listings: list[str]
) -> None:
    ran = await lane.run(_call("get_issue"))
    assert ran.disposition.status == "completed", ran.disposition
    assert listings == [world.MCP]
    assert len(_intents(ran, GET_ISSUE)) == 1, "the call reached the server's tool, granted"


async def test_r31hot_3_the_servers_create_asks_on_the_job_when_nobody_set_a_rule(lane: _Lane) -> None:
    ran = await lane.run(_call("create_issue"))
    assert _held(ran, CREATE_ISSUE) == "writes"


async def test_r31hot_4_an_explicit_allow_runs_the_create_on_the_job(lane: _Lane) -> None:
    _own_rules(lane, {"create_issue": "allow"})
    ran = await lane.run(_call("create_issue"))
    assert ran.disposition.status == "completed", ran.disposition
    assert len(_intents(ran, CREATE_ISSUE)) == 1


async def test_r31hot_5_a_block_refuses_the_call(lane: _Lane) -> None:
    _own_rules(lane, {"get_issue": "block"})
    ran = await lane.run(_call("get_issue"))
    assert [e.data["rule"] for e in ran.of("tool.denied")] == ["tool_rules"]
    assert not _intents(ran, GET_ISSUE)


async def test_r31hot_6_a_deployment_ask_on_the_tool_holds_it(lane: _Lane) -> None:
    _deployment_rules(lane, {GET_ISSUE: "ask"})
    assert _held(await lane.run(_call("get_issue")), GET_ISSUE) == "tool"


async def test_r31hot_7_the_flow_guard_wins_over_an_allow_when_the_parent_read_what_the_job_cannot_see(
    lane: _Lane,
) -> None:
    _own_rules(lane, {"create_issue": "allow"})
    ran = await lane.run(_call("create_issue"), inherited_label=None)
    assert _held(ran, CREATE_ISSUE) == "flow_external_write"

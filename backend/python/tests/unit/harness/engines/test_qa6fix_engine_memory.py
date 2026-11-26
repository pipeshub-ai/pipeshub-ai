"""QA6-F7: an engine run is offered ``remember`` as the durable loop is.

``PipesHubEngineAssembler`` and the engine kernel as a worker runs them, over a run claimed from a live Node whose spec
pins the memory write scopes (``x.pipeshub.memory``), with a scripted engine in a fake sandbox. The engine reaches the
tool through the run's tool gateway, so the proposal is judged by the run's approval policy, journaled under the
attempt's fence and sent to Node with the attempt's own credential; nothing is written from inside the sandbox.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.assembly.tool_sources import PLATFORM
from app.harness.engines.durable import DurableEngineServices, PipesHubEngineAssembler
from app.harness.labels import own_work_label, tool_kind
from harness_core.clock import SystemClock
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.durable.kernel import EngineKernel
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.gateway.asking import ASK_USER, asks_person
from harness_core.gateway.types import GatewayClaims
from harness_core.ids import RandomIds
from harness_core.memories import REMEMBER_TOOL
from harness_core.sandbox.types import RuntimeMode
from harness_core.testing import ModelScript, Reply, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import (
    FAKE_VERSION,
    IMAGE,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
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
    from collections.abc import Iterator, Sequence

    from harness_core.driver.types import Disposition

    from harness_core.engines.types import EngineProfile
    from harness_core.journal.types import JournalEvent
    from tests.unit.harness.test_node_journal_contract import (
        _NodeServer,  # pyright: ignore[reportPrivateUsage]
    )

__all__ = ["node_server"]

NODE_ORG = "org-contract"
REMEMBER_ID = f"{PLATFORM}@memory/{REMEMBER_TOOL}"
APPROVALS = "/services/agentHarness/approvals"
LIKES_TEA = {"content": "Likes tea.", "kind": "preference", "scope": "user"}


def remember(**arguments: str) -> str:
    """The scripted engine's line that calls the tool, in the shape its listed schema asks for: the gateway checks
    an engine's arguments against it (TG-05)."""
    return f"tool {REMEMBER_TOOL} {json.dumps({'memories': [{**LIKES_TEA, **arguments}]})}"


@dataclasses.dataclass
class _Ran:
    disposition: Disposition
    run_id: str
    events: list[JournalEvent]

    def of(self, event_type: str) -> list[JournalEvent]:
        return [e for e in self.events if e.type == event_type and e.run_id == self.run_id]


class _Engine:
    """Engine runs of the live Node's org, assembled and driven as the worker does it."""

    def __init__(self, server: _NodeServer) -> None:
        self._node = NodeHarness(server)
        self.control = server.control
        _ = self.control.post("/memory/policy", json={"mode": "user", "review": "auto"}).raise_for_status()
        self.services = world.Services(org=NODE_ORG)
        clock = SystemClock()
        host = InMemoryToolGatewayHost()
        # The scripted engine's model, reached through the sandbox's egress proxy as a real engine reaches its own.
        brain = ConformanceGateway(endpoints=EngineControllerConfig(images={}).endpoints("unused-0123456789", "unused-0123456789"), grant=[])
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
        script = ScriptedModel(ModelScript(id="qa6fix", turns=[Turn(reply=Reply(text="ok"))]))
        self.assembler = PipesHubEngineAssembler(self.services, engines, model_factory=world.model_factory(ScriptedModelPort(script)))

    def _kernel(self) -> EngineKernel:
        # No hold: a held call suspends the run at once instead of waiting here for a person.
        return EngineKernel(
            journal=self._node.node,
            assembler=self.assembler,
            clock=SystemClock(),
            broker=self._broker,
            poll_s=0.005,
            flush_interval_s=0.005,
            approval_hold_s=0.0,
        )

    async def _claim(self, write_scopes: Sequence[str] | None, script: str, recalled: Sequence[str] = ()) -> Any:  # noqa: ANN401 - the journal's claim
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        executor = engine_run_spec().executor
        assert executor is not None
        spec = world.pinned_spec(tool_ids=[f"jira@{world.JIRA}/search_issues"], executor=dump_contract(executor))
        pinned = cast("dict[str, Any]", dump_contract(spec))
        if write_scopes is not None:
            pinned["extensions"]["x.pipeshub"]["memory"] = {"recalled": list(recalled), "writeScopes": list(write_scopes)}
        self._node.admit(run_id, session_id=f"session-{run_id}", input_text=script, spec=validate_contract(RunSpec, pinned))
        claim = await self._node.journal.claim(run_id, worker_id="worker-qa6fix")
        assert claim is not None
        return claim

    async def offered(
        self, write_scopes: Sequence[str] | None, profile: EngineProfile | None = None, recalled: Sequence[str] = ()
    ) -> dict[str, Any]:
        """The tools the run's gateway lists for the engine, by name."""
        claim = await self._claim(write_scopes, "say hello", recalled)
        runtime = await self.assembler.build(self._kernel().attempt_of(claim, profile or engine_profile()))
        try:
            claims = GatewayClaims(runtime.tool_binding, kid="qa6fix", expires_at=4_102_444_800)
            listed = {tool.name: tool for tool in await runtime.tools.list_tools(claims)}
            # The grant, and the gateway's own ask_user for a run with a person to ask (WP-10, C3.70).
            assert (ASK_USER in listed) is asks_person(claim.spec)
            granted = {name: tool for name, tool in listed.items() if name != ASK_USER}
            assert runtime.grant_size == len(granted)
            return granted
        finally:
            if runtime.close is not None:
                await runtime.close()

    async def run(self, write_scopes: Sequence[str] | None, script: str) -> _Ran:
        claim = await self._claim(write_scopes, script)
        disposition = await self._kernel().run_claimed(claim, claim_sent_at=SystemClock().suspend_aware())
        events = await self._node.node.read(claim.stream_id, after_seq=0)
        return _Ran(disposition, claim.run_id, events)

    def proposals(self, run_id: str) -> list[dict[str, Any]]:
        listed = self.control.get("/memory/list", params={"runId": run_id}).json()["memories"]
        return cast("list[dict[str, Any]]", listed)


@pytest.fixture
def _deployment() -> Iterator[None]:
    with world.deployment():
        yield


@pytest.fixture
def engine(node_server: _NodeServer, _deployment: None) -> _Engine:
    return _Engine(node_server)


# F7-1


async def test_f7_1_an_engine_run_whose_pin_grants_write_scopes_is_offered_remember(engine: _Engine) -> None:
    listed = await engine.offered(["user", "session"])
    assert REMEMBER_TOOL in listed
    assert listed[REMEMBER_TOOL].input_schema["properties"]["memories"]["items"]["properties"]["scope"]["enum"] == ["user", "session"]


# F7-2


@pytest.mark.parametrize("write_scopes", [None, []])
async def test_f7_2_no_pin_or_no_scope_offers_nothing_and_changes_nothing_else(engine: _Engine, write_scopes: list[str] | None) -> None:
    without = await engine.offered(write_scopes)
    granted = await engine.offered(["user"])
    assert REMEMBER_TOOL not in without
    assert sorted(set(granted) - {REMEMBER_TOOL}) == sorted(without)


async def test_f7_2_a_pin_that_only_recalls_offers_no_tool(engine: _Engine) -> None:
    assert REMEMBER_TOOL not in await engine.offered([], recalled=["mem_pref"])


async def test_f7_2_an_engine_that_calls_it_anyway_keeps_nothing(engine: _Engine) -> None:
    ran = await engine.run(None, f"{remember()}\nsay done")
    assert ran.disposition.status == "completed"
    assert engine.proposals(ran.run_id) == []


# F7-3


async def test_f7_3_the_proposal_goes_through_the_gateway_with_the_attempts_own_credential(engine: _Engine) -> None:
    ran = await engine.run(["user"], f"{remember()}\nsay noted")
    assert ran.disposition.status == "completed"
    [stored] = engine.proposals(ran.run_id)
    assert (stored["content"], stored["scope"], stored["kind"], stored["origin"]) == ("Likes tea.", "user", "preference", "model")
    assert stored["ownerId"] == "actor-contract" and stored["sessionId"] == f"session-{ran.run_id}"
    [outcome] = ran.of("tool.outcome")
    assert outcome.data["status"] == "ok" and outcome.writer == "worker" and outcome.fence == 1


async def test_f7_3_a_scope_the_pin_does_not_grant_keeps_nothing(engine: _Engine) -> None:
    ran = await engine.run(["session"], f"{remember(scope='user')}\nsay done")
    assert ran.disposition.status == "completed"
    assert engine.proposals(ran.run_id) == []


async def test_f7_3_the_same_proposal_twice_is_one_memory(engine: _Engine) -> None:
    ran = await engine.run(["user"], f"{remember()}\n{remember()}\nsay noted")
    assert ran.disposition.status == "completed"
    assert len(engine.proposals(ran.run_id)) == 1


# F7-4


async def test_f7_4_a_writes_rule_does_not_hold_it_and_a_rule_naming_the_tool_does(engine: _Engine) -> None:
    engine.services.config.put(APPROVALS, {"default": {"writes": "ask"}})
    unheld = await engine.run(["user"], f"{remember()}\nsay noted")
    assert unheld.disposition.status == "completed" and len(engine.proposals(unheld.run_id)) == 1
    engine.services.config.put(APPROVALS, {"default": {"tools": {REMEMBER_ID: "ask"}}})
    held = await engine.run(["user"], f"{remember()}\nsay waiting")
    assert held.disposition.status == "suspended"
    assert engine.proposals(held.run_id) == []
    [reason] = held.of("interaction.explained")
    assert (reason.data["rule"], reason.data["layer"], reason.data["toolId"]) == ("tool", "deployment", REMEMBER_ID)
    assert len(held.of("interaction.requested")) == 1


async def test_f7_4_a_rule_that_denies_the_tool_refuses_it(engine: _Engine) -> None:
    engine.services.config.put(APPROVALS, {"default": {"tools": {REMEMBER_ID: "deny"}}})
    ran = await engine.run(["user"], f"{remember()}\nsay done")
    assert ran.disposition.status == "completed"
    assert engine.proposals(ran.run_id) == []
    [denied] = ran.of("tool.denied")
    assert denied.data["rule"] == "policy"


# F7-5


async def test_f7_5_remembering_adds_nothing_to_what_the_run_read(engine: _Engine) -> None:
    ran = await engine.run(["user"], f"{remember()}\nsay noted")
    assert tool_kind(REMEMBER_TOOL) == "none" and tool_kind(REMEMBER_ID) == "none"
    assert ran.of("tool.outcome")
    label = own_work_label(ran.events, ran.run_id, tool_names={REMEMBER_ID: REMEMBER_TOOL})
    assert label.complete and not label.sources


# F7-6


async def test_f7_6_a_profile_without_the_workspace_class_withholds_it(engine: _Engine) -> None:
    profile = engine_profile()
    narrowed = dataclasses.replace(profile, tools=dataclasses.replace(profile.tools, effects=frozenset({"read"})))
    assert REMEMBER_TOOL not in await engine.offered(["user"], narrowed)

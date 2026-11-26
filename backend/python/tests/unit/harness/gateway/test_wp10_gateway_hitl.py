"""WP-10 at the tool gateway, over real HTTP and the MCP SDK client (as G2's suite is).

AP-03: an approval holds once: the approved call runs once, an identical re-call in the run answers from the log,
and the same call in a later run asks again. AP-05: a denial's reason reaches the engine in the call's error.
AP-07: an expired decision is a refusal; the call never runs. C3.29 / C3.70: ``ask_user`` is the gateway's own tool,
listed only when the run has a person to ask, and asking runs nothing; an engine that reserves the bare name sees it
under its alias.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from harness_core.effects import EffectRunner
from harness_core.effects.policy import ENGINE_APPROVALS
from harness_core.engines.types import EngineCapabilities
from harness_core.gateway import GatewayClaims, RunToolEndpoint
from harness_core.gateway.asking import (
    ALREADY_ANSWERED,
    ASK_USER,
    ASK_USER_ALIAS,
    INPUT_PENDING,
    INVALID_QUESTION,
    AskedQuestion,
    asks_person,
    question_key,
)
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.gateway.types import RunAttemptBinding, grant_digest
from harness_core.interactions import BridgeApprovalHold, InMemoryInteractionBridge
from harness_core.interactions.ports import RunBinding
from harness_core.invoker import InvocationScope
from harness_core.kernel.durable import classify_tool_effect
from harness_core.testing.ids import SeededIds
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder
from tests.unit.harness.gateway.support import GatewayServer, served
from tests.unit.harness.gateway.test_g2_gateway_attempts import (
    _READ,  # pyright: ignore[reportPrivateUsage]
    _WRITE,  # pyright: ignore[reportPrivateUsage]
    _IntentLog,  # pyright: ignore[reportPrivateUsage]
    _Knowledge,  # pyright: ignore[reportPrivateUsage]
    _Tickets,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.effects import ToolEffect

GRANT = {"knowledge__lookup": _READ, "tickets__create_ticket": _WRITE}


class Run:
    """One run's endpoint over a shared world (its tools and what they did), with its own effect log and binding."""

    def __init__(
        self,
        run_id: str,
        runs: list[str],
        bridge: InMemoryInteractionBridge,
        *,
        asks: bool = True,
        capabilities: EngineCapabilities | None = None,
        answered: dict[str, str] | None = None,
    ) -> None:
        registry = ToolRegistry()
        for name, toolset in (("knowledge", _Knowledge), ("tickets", _Tickets)):
            ToolsetBuilder(toolset(runs), name=name, description=name, path_prefix=f"/tools/{name}").register_into(
                registry
            )
        effects: dict[str, ToolEffect] = {n: classify_tool_effect(n, t) for n, t in GRANT.items()}
        self.binding = RunAttemptBinding(
            tenant="org-1",
            scope="session-1",
            run_id=run_id,
            parent_run_id=None,
            attempt_id=f"{run_id}.a1",
            fence=1,
            tool_ids_digest=grant_digest(e.tool_id for e in effects.values()),
            routes_digest=grant_digest(()),
        )
        self.claims = GatewayClaims(self.binding, kid="wp10", expires_at=4_102_444_800)
        self.log = _IntentLog()
        hold = BridgeApprovalHold(bridge, RunBinding("org-1", "session-1", run_id), hold_s=0.05)
        self.endpoint = RunToolEndpoint(
            binding=self.binding,
            catalog=RegistryToolPort(registry, effects),
            effects=EffectRunner(self.log, ENGINE_APPROVALS, hold=hold),
            scope=InvocationScope(run_id, "org-1", self.binding.attempt_id),
            ids=SeededIds(),
            citations=InMemoryCitationSink(),
            hold_s=0.2,
            asks=asks,
            capabilities=capabilities,
            answered=answered,
        )


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


async def test_ap03_approve_once_runs_once_and_a_later_run_asks_again(server: GatewayServer) -> None:
    runs: list[str] = []
    bridge = InMemoryInteractionBridge()
    first = Run("run-a", runs, bridge)
    async with served(server, first.endpoint) as client:
        held = await client.call_tool(first.claims, "tickets__create_ticket", {"q": "x"})
        assert held.interaction_id is not None
        bridge.decide(held.interaction_id, "approved")
        approved = await client.call_tool(first.claims, "tickets__create_ticket", {"q": "x"})
        again = await client.call_tool(first.claims, "tickets__create_ticket", {"q": "x"})
    assert (approved.disposition, again.disposition) == ("executed", "recorded")
    assert runs == ["tickets__create_ticket:x"]
    later = Run("run-b", runs, bridge)
    async with served(server, later.endpoint) as client:
        asked = await client.call_tool(later.claims, "tickets__create_ticket", {"q": "x"})
    assert (asked.disposition, asked.reason_code) == ("deferred", "approval_pending")
    assert asked.interaction_id not in (None, held.interaction_id)
    assert runs == ["tickets__create_ticket:x"]


async def test_ap05_a_denial_decided_during_the_hold_tells_the_engine_why(server: GatewayServer) -> None:
    runs: list[str] = []
    bridge = InMemoryInteractionBridge()
    run = Run("run-a", runs, bridge)
    reason = "Open it in the SUPPORT project instead"
    async with served(server, run.endpoint) as client:
        calling = asyncio.ensure_future(client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"}))
        for _ in range(500):
            if bridge.requests:
                break
            await asyncio.sleep(0.001)
        bridge.decide(next(iter(bridge.requests)), "denied", said=reason)
        denied = await calling
        recall = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
    for result in (denied, recall):
        assert (result.is_error, result.disposition, result.retryable) == (True, "denied", False)
        assert json.dumps(reason) in result.text
        assert "Do not call it again with the same arguments" in result.text
    assert runs == [] and run.log.intents == []


async def test_ap07_an_expired_approval_is_a_refusal_and_never_runs(server: GatewayServer) -> None:
    """AP-07: past the wait window nobody can approve (Node's sweeper ends the run: PW-2; the card reads expired: TO-2);
    a decision that says ``expired`` is never an approval."""
    runs: list[str] = []
    bridge = InMemoryInteractionBridge()
    run = Run("run-a", runs, bridge)
    async with served(server, run.endpoint) as client:
        held = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
        assert held.interaction_id is not None
        bridge.decide(held.interaction_id, "expired")
        recall = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
    assert (recall.is_error, recall.disposition) == (True, "denied")
    assert runs == [] and run.log.intents == []


async def test_c3_70_ask_user_is_listed_and_asking_runs_nothing(server: GatewayServer) -> None:
    runs: list[str] = []
    run = Run("run-a", runs, InMemoryInteractionBridge())
    async with served(server, run.endpoint) as client:
        listed = await client.list_tools(run.claims)
        asked = await client.call_tool(run.claims, ASK_USER, {"question": "Which project?", "options": ["A", "B", "A"]})
        bad = await client.call_tool(run.claims, ASK_USER, {"question": "  "})
    assert [t.name for t in listed][-1] == ASK_USER
    assert (asked.is_error, asked.disposition, asked.reason_code, asked.retryable) == (
        False,
        "asked",
        INPUT_PENDING,
        False,
    )
    assert asked.invocation_id == AskedQuestion("Which project?", ("A", "B")).call_id
    assert "End this turn" in asked.text
    assert (bad.is_error, bad.disposition, bad.reason_code, bad.retryable) == (True, "refused", INVALID_QUESTION, True)
    assert runs == [] and run.log.intents == []


async def test_ac2_a_question_the_person_already_answered_is_answered_from_the_task(server: GatewayServer) -> None:
    """ANSWER-CONTRACT-1 (ADR-RUN-03 D2): a job is handed the person's answer with its request; asking the same
    question again gets that answer, never a second card. Another question still asks."""
    runs: list[str] = []
    run = Run("run-a", runs, InMemoryInteractionBridge(), answered={question_key("Which launch do you mean?"): "Orbit GA"})
    async with served(server, run.endpoint) as client:
        again = await client.call_tool(run.claims, ASK_USER, {"question": "which launch do you mean ", "options": ["x"]})
        other = await client.call_tool(run.claims, ASK_USER, {"question": "Which quarter?"})
    assert (again.is_error, again.disposition, again.reason_code) == (False, "executed", ALREADY_ANSWERED)
    assert "“Orbit GA”" in again.text and "do not ask it again" in again.text
    assert (other.disposition, other.reason_code) == ("asked", INPUT_PENDING)
    assert runs == [] and run.log.intents == []


async def test_c3_29_a_run_with_nobody_to_ask_has_no_ask_user(server: GatewayServer) -> None:
    runs: list[str] = []
    run = Run("run-a", runs, InMemoryInteractionBridge(), asks=False)
    async with served(server, run.endpoint) as client:
        listed = await client.list_tools(run.claims)
        refused = await client.call_tool(run.claims, ASK_USER, {"question": "Which project?"})
    assert ASK_USER not in [t.name for t in listed]
    assert (refused.is_error, refused.disposition) == (True, "refused")


async def test_c3_70_an_engine_that_reserves_ask_user_is_offered_it_under_its_alias(server: GatewayServer) -> None:
    """Gemini CLI in ACP mode drops every MCP tool whose unprefixed name is ``ask_user`` (its own, excluded there)."""
    runs: list[str] = []
    gemini = EngineCapabilities(
        can_steer=False,
        native_skills=True,
        sub_agents=False,
        model_apis=frozenset(),
        max_tools=None,
        drive="stdio",
        reserved_tool_names=frozenset({ASK_USER}),
    )
    run = Run("run-a", runs, InMemoryInteractionBridge(), capabilities=gemini)
    async with served(server, run.endpoint) as client:
        listed = [t.name for t in await client.list_tools(run.claims)]
        asked = await client.call_tool(run.claims, ASK_USER_ALIAS, {"question": "Which project?"})
        bare = await client.call_tool(run.claims, ASK_USER, {"question": "Which project?"})
    assert listed[-1] == ASK_USER_ALIAS and ASK_USER not in listed
    assert (asked.disposition, asked.reason_code) == ("asked", INPUT_PENDING)
    assert asked.invocation_id == AskedQuestion("Which project?").call_id
    assert (bare.is_error, bare.disposition) == (True, "refused")
    assert runs == [] and run.log.intents == []


@pytest.mark.parametrize(
    ("kind", "parent_asks", "asks"),
    # ANSWER-CONTRACT-1 (ADR-RUN-03 D2): a job asks too; its parent shows the card in its own chat. FU-3: only a job
    # whose parent can ask a person (pinned at the job's admission): a schedule's job never waits on nobody.
    [
        ("interactive", False, True),
        ("child", True, True),
        ("child", False, False),
        ("scheduled", True, False),
        ("workflow_code", False, False),
        ("command", False, False),
    ],
)
def test_c3_29_a_run_with_a_person_in_the_chat_or_its_job_asks(kind: str, parent_asks: bool, asks: bool) -> None:
    spec = minimal_run_spec().model_copy(update={"run_kind": kind})
    assert asks_person(spec, parent_asks=parent_asks) is asks

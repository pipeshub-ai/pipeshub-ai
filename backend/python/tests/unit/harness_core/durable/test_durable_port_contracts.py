"""WRK-G4: the durable kernel's ports against their contracts (DP1..DP18), once per adapter
``harness_core`` holds: the in-memory references of ``harness_core.testing`` and the core's own
production adapters. The PipesHub adapters run the same contracts in
``tests/unit/harness/assembly/test_durable_port_contracts.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

import pytest

from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import LedgerChildBudget, RunBudgetView
from harness_core.budget.types import Amount, Cap, RunAccountRef
from harness_core.effects.types import ToolExecution
from harness_core.kernel.core.messages import (
    ToolCall,
    UserMessage,
)
from harness_core.kernel.durable.adapters import StreamingModelPort, TransportModelPort
from harness_core.kernel.durable.delegation import (
    Delegated,
    DelegatingTool,
    DelegationView,
    InterimChildBudget,
    NamedDelegation,
    goal_tool,
)
from harness_core.kernel.durable.ports import ModelCall
from harness_core.kernel.durable.routing import RoutedModelPort
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.kernel.durable.transcript import PlainImportedHistory
from harness_core.kernel.fold import RunState
from harness_core.logs import HarnessLog
from harness_core.memories.memory import InMemoryMemorySource
from harness_core.memories.recall import (
    MEMORY_SLOT,
    MemoryContextHost,
    MemoryContextShaper,
    MemoryRecallStep,
)
from harness_core.memories.types import RecalledMemory
from harness_core.models.policy import ModelPolicy, PolicyRoute
from harness_core.models.stream_watch import ModelStalled
from harness_core.models.transport_model import TransportModel
from harness_core.testing import ErrorSpec, ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.after_run import AfterRunCase, AfterRunStepContract
from harness_core.testing.contracts.durable_ports import (
    CapabilityCheckContract,
    DurableModelPortContract,
    DurableToolPortContract,
    GateCase,
    ImportedHistoryContract,
    InputRendererContract,
    PostRunStepContract,
    PreRunGateContract,
    PreRunStepContract,
    RunPresenterContract,
    RunRestoreContract,
)
from harness_core.testing.contracts.durable_turns import (
    ChildBudgetContract,
    ChildResultShaperContract,
    ContextHostContract,
    DelegationToolsContract,
    LoopDirectorContract,
    ResponseReviewContract,
    RoutePortsCase,
    RoutePortsContract,
    TurnShaperContract,
)
from harness_core.testing.durable import (
    FunctionToolPort,
    ScriptedModelPort,
    provider_events,
)
from harness_core.testing.durable_ports import (
    AcceptingResponseReview,
    DefaultContextHost,
    IdentityChildResultShaper,
    IdleLoopDirector,
    LedgerAfterRunStep,
    PassThroughTurnShaper,
    PlainInputRenderer,
    SilentRunPresenter,
    SlotPreRunGate,
    SlotRunRestore,
    StaticCapabilityCheck,
    StaticPostRunStep,
    StaticPreRunStep,
    StaticRoutePorts,
)
from harness_core.testing.model_gateway import RecordedTransport
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.invoker.catalogs import RegistryCatalogs

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.effects.types import ToolContext
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.durable.ports import DurableModelPort, RunRestore
    from harness_core.testing.contracts.tool_catalog import OfferedTool

LOG = HarnessLog.named("tests.durable_port_contracts")
T = TypeVar("T")

# ---- DP1 DurableModelPort ----


def _scripted(reply: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="dp1", turns=[Turn(reply=reply)])))


class ScriptedPorts:
    def port(self, reply: Reply) -> ScriptedModelPort:
        return _scripted(reply)

    def failing(self) -> ScriptedModelPort:
        return _scripted(Reply(error=ErrorSpec(kind="server_error")))

    def rate_limited(self) -> ScriptedModelPort:
        return _scripted(Reply(error=ErrorSpec(kind="rate_limited", retry_after_s=2.0)))


class TransportPorts(Generic[T]):
    """``TransportModelPort`` or ``StreamingModelPort`` over a recorded provider. A stall is the
    one provider failure these layers classify themselves; the rest is ``ClassifiedModelPort``'s."""

    def __init__(self, port_type: Callable[..., T]) -> None:
        self._type = port_type

    def _over(self, transport: RecordedTransport) -> T:
        return self._type(TransportModel(transport), provider="openai", transport="direct")

    def port(self, reply: Reply) -> T:
        return self._over(RecordedTransport(provider_events(reply)))

    def failing(self) -> T:
        return self._over(RecordedTransport(fail=ModelStalled("first_event", 30.0, 0)))

    def rate_limited(self) -> None:
        return None


class RoutedPorts:
    """``RoutedModelPort`` on its primary route, as an assembler builds it from the pinned policy."""

    def _routed(self, inner: DurableModelPort) -> RoutedModelPort:
        return RoutedModelPort(ModelPolicy.from_spec(minimal_run_spec().model), inner)

    def port(self, reply: Reply) -> RoutedModelPort:
        return self._routed(_scripted(reply))

    def failing(self) -> RoutedModelPort:
        return self._routed(ScriptedPorts().failing())

    def rate_limited(self) -> RoutedModelPort:
        return self._routed(ScriptedPorts().rate_limited())


class TestScriptedModelPort(DurableModelPortContract):
    @pytest.fixture
    def model_ports(self) -> ScriptedPorts:  # type: ignore[override]
        return ScriptedPorts()


class TestTransportModelPort(DurableModelPortContract):
    @pytest.fixture
    def model_ports(self) -> TransportPorts[TransportModelPort]:  # type: ignore[override]
        return TransportPorts(TransportModelPort)


class TestStreamingModelPort(DurableModelPortContract):
    @pytest.fixture
    def model_ports(self) -> TransportPorts[StreamingModelPort]:  # type: ignore[override]
        return TransportPorts(StreamingModelPort)


class TestRoutedModelPort(DurableModelPortContract):
    @pytest.fixture
    def model_ports(self) -> RoutedPorts:  # type: ignore[override]
        return RoutedPorts()


# ---- DP2 DurableToolPort ----


class FunctionToolPorts:
    """``FunctionToolPort`` holding the granted tools only (it has no grant of its own)."""

    def __init__(self) -> None:
        self.runs: list[str] = []

    def build(self, offered: Sequence[OfferedTool], grant: Collection[str]) -> FunctionToolPort:
        port = FunctionToolPort()
        for tool in offered:
            if tool.effect.tool_id in grant:
                port.add(tool.name, self._run(tool.name), effect=tool.effect)
        return port

    def _run(self, name: str) -> Callable[[Mapping[str, JsonValue], ToolContext], Awaitable[ToolExecution]]:
        async def run(arguments: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
            self.runs.append(name)
            return ToolExecution(status="ok", content=f"{name}:{arguments.get('q')}")

        return run


class TestFunctionToolPort(DurableToolPortContract):
    @pytest.fixture
    def durable_tools(self) -> FunctionToolPorts:  # type: ignore[override]
        return FunctionToolPorts()


class TestRegistryToolPort(DurableToolPortContract):
    @pytest.fixture
    def durable_tools(self) -> RegistryCatalogs:  # type: ignore[override]
        return RegistryCatalogs()


# ---- DP3 PreRunStep, DP4 PostRunStep, DP5 PreRunGate, DP6 CapabilityCheck ----


class TestStaticPreRunStep(PreRunStepContract):
    @pytest.fixture
    def pre_run_step(self) -> StaticPreRunStep:
        return StaticPreRunStep("intent", {"route": "search", "confidence": 0.9, "tags": ["deploy"]})


PREFERENCE = RecalledMemory("mem_pref", "user", "preference", "Answer in bullet points.", {"complete": True, "sources": []})


class TestMemoryRecallStep(PreRunStepContract):
    @pytest.fixture
    def pre_run_step(self) -> MemoryRecallStep:
        return MemoryRecallStep(InMemoryMemorySource([PREFERENCE]), log=LOG)


class TestStaticPostRunStep(PostRunStepContract):
    @pytest.fixture
    def post_run_step(self) -> StaticPostRunStep:
        return StaticPostRunStep("learning")


class TestSlotPreRunGate(PreRunGateContract):
    @pytest.fixture
    def gate_case(self) -> GateCase:
        slot = PRE_RUN_SLOT_PREFIX + "clarify"
        return GateCase(SlotPreRunGate(slot), asks={slot: ["Which service?"]}, proceeds={slot: []})


class TestStaticCapabilityCheck(CapabilityCheckContract):
    @pytest.fixture
    def capability_check(self) -> StaticCapabilityCheck:
        return StaticCapabilityCheck({"mcp:github": "sha256:" + "1" * 64, "tools": ["lookup", "create"]})


# ---- DP7 RunPresenter, DP8 RunRestore, DP9 InputRenderer, DP12 ImportedHistory ----


class TestSilentRunPresenter(RunPresenterContract):
    @pytest.fixture
    def presenters(self) -> Callable[[], SilentRunPresenter]:  # type: ignore[override]
        return SilentRunPresenter


class SlotRestores:
    def fresh(self) -> SlotRunRestore:
        return SlotRunRestore()

    async def live(self) -> tuple[SlotRunRestore, list[NewEvent]]:
        live = SlotRunRestore()
        return live, [live.save({"phase": "plan"}), live.save({"phase": "act", "steps": ["a", "b"]})]

    def state(self, restore: RunRestore) -> JsonValue:
        assert isinstance(restore, SlotRunRestore)
        return dict(restore.slots)


class TestSlotRunRestore(RunRestoreContract):
    @pytest.fixture
    def restores(self) -> SlotRestores:  # type: ignore[override]
        return SlotRestores()


class TestPlainInputRenderer(InputRendererContract):
    @pytest.fixture
    def renderers(self) -> Callable[[], PlainInputRenderer]:  # type: ignore[override]
        return PlainInputRenderer


class TestPlainImportedHistory(ImportedHistoryContract):
    @pytest.fixture
    def imported_history(self) -> PlainImportedHistory:
        return PlainImportedHistory()


# ---- DP10 TurnShaper, DP11 ResponseReview, DP15 ContextHost ----

MEMORY_STATE = RunState(
    run_id="run-1",
    slots={MEMORY_SLOT: {"text": "Answer in bullet points.", "entries": [], "chars": 24, "truncated": False}},
)


class Fresh(Generic[T]):
    """A ``TurnHarness`` over a factory, the memory slot journaled, one user input."""

    def __init__(self, make: Callable[[], T], state: RunState = MEMORY_STATE) -> None:
        self._make = make
        self._state = state

    def fresh(self) -> T:
        return self._make()

    def state(self) -> RunState:
        return self._state

    def call(self) -> ModelCall:
        return ModelCall(messages=[UserMessage(content="When did the deploy finish?")], system="Be brief.", input_index=0)


class TestPassThroughTurnShaper(TurnShaperContract):
    @pytest.fixture
    def shapers(self) -> Fresh[PassThroughTurnShaper]:  # type: ignore[override]
        return Fresh(PassThroughTurnShaper)


class TestMemoryContextShaper(TurnShaperContract):
    @pytest.fixture
    def shapers(self) -> Fresh[MemoryContextShaper]:  # type: ignore[override]
        return Fresh(lambda: MemoryContextShaper(PassThroughTurnShaper()))


class TestAcceptingResponseReview(ResponseReviewContract):
    @pytest.fixture
    def reviews(self) -> Fresh[AcceptingResponseReview]:  # type: ignore[override]
        return Fresh(AcceptingResponseReview)


class TestDefaultContextHost(ContextHostContract):
    @pytest.fixture
    def hosts(self) -> Fresh[DefaultContextHost]:  # type: ignore[override]
        return Fresh(DefaultContextHost)


class TestMemoryContextHost(ContextHostContract):
    @pytest.fixture
    def hosts(self) -> Fresh[MemoryContextHost]:  # type: ignore[override]
        return Fresh(lambda: MemoryContextHost(DefaultContextHost()))


# ---- DP13 ChildResultShaper, DP14 LoopDirector ----


class TestIdentityChildResultShaper(ChildResultShaperContract):
    @pytest.fixture
    def child_result(self) -> IdentityChildResultShaper:
        return IdentityChildResultShaper()


class TestIdleLoopDirector(LoopDirectorContract):
    @pytest.fixture
    def directors(self) -> Fresh[IdleLoopDirector]:  # type: ignore[override]
        return Fresh(IdleLoopDirector)


# ---- DP16 DelegationTools, DP17 ChildBudget, DP18 RoutePorts ----

DELEGATE = ToolCall(id="c1", name="delegate", arguments={"goal": "List the open tickets", "task_id": "tickets"})


class NamedDelegations:
    def fresh(self) -> NamedDelegation:
        return NamedDelegation({"delegate": DelegatingTool(goal_tool("delegate", "Hand one task to a sub-agent"))})

    def ready(self) -> tuple[Delegated, DelegationView]:
        pending = Delegated("t1.0", DELEGATE, 1, "pending")
        return pending, DelegationView((pending,))


class TestNamedDelegation(DelegationToolsContract):
    @pytest.fixture
    def delegations(self) -> NamedDelegations:  # type: ignore[override]
        return NamedDelegations()


class TestInterimChildBudget(ChildBudgetContract):
    @pytest.fixture
    def child_budget(self) -> InterimChildBudget:
        return InterimChildBudget()


class TestLedgerChildBudget(ChildBudgetContract):
    @pytest.fixture
    def child_budget(self) -> LedgerChildBudget:
        return LedgerChildBudget(RunBudgetView())


PRIMARY = PolicyRoute(index=0, provider="openai", model="gpt-x", route_id="openai:gpt-x")
FALLBACK = PolicyRoute(index=1, provider="azureopenai", model="gpt-y", route_id="azureopenai:gpt-y")


class TestStaticRoutePorts(RoutePortsContract):
    @pytest.fixture
    def route_ports(self) -> RoutePortsCase:
        ports = StaticRoutePorts({PRIMARY.route_id: _scripted(Reply(text="ok"))})
        return RoutePortsCase(ports, reachable=PRIMARY, unreachable=FALLBACK)


ACCOUNT = RunAccountRef("org-dp19", "run-dp19")


async def _ledger_case() -> AfterRunCase:
    ledger = InMemoryBudgetLedger(ManualClock())
    _ = await ledger.open_root(ACCOUNT, Cap(None, 1_000_000))
    step = LedgerAfterRunStep(ledger, ACCOUNT, Amount(0, 100))
    failing = LedgerAfterRunStep(ledger, ACCOUNT, Amount(0, 100), fail=True)

    async def held() -> int:
        return (await ledger.remaining(ACCOUNT)).reserved.tokens

    async def charged() -> int:
        return (await ledger.remaining(ACCOUNT)).committed.tokens

    return AfterRunCase(step, failing, lambda: len(step.done), held, charged)


class TestLedgerAfterRunStep(AfterRunStepContract):
    @pytest.fixture
    async def after_run_case(self) -> AfterRunCase:
        return await _ledger_case()

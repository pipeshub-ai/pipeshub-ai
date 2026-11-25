"""WRK-G4: the PipesHub adapters of the durable kernel's ports against the contracts their
in-memory references pass (``tests/unit/harness_core/durable/test_durable_port_contracts.py``).

Each "fresh" instance comes from a runtime ``PipesHubRuntimeAssembler`` built for its own claim,
as a re-claimed attempt gets one; only the outermost I/O is the assembly world's fakes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Generic, TypeVar, override

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.assembly.deep import (
    DEEP_PHASE_SLOT,
    DeepDelegation,
    DeepDirector,
    DeepPhase,
    SubAgentResult,
)
from app.harness.assembly.followups import FollowupsStep
from app.harness.assembly.history import LegacyHistory
from app.harness.assembly.loop import (
    GOAL_SLOT,
    ClarificationGate,
    Composition,
    CompositionStep,
    GoalStep,
    PipesHubResponseReview,
    PipesHubTurnShaper,
    SubAgentGoalStep,
)
from app.harness.assembly.model import ClassifiedModelPort, ResolvedModel
from app.harness.assembly.model_policy import ConfiguredRoutePorts
from app.harness.assembly.skill_learning import SkillLearningStep, agent_result
from app.harness.assembly.steering import PipesHubInputRenderer
from app.harness.assembly.tool_environment import UnavailableItem, UnavailableItemsStep
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.presentation import PipesHubPresenter
from app.utils.chat_helpers import CitationRefMapper, RecordIdShortener
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall, UserMessage
from harness_core.kernel.durable.adapters import StreamingModelPort
from harness_core.kernel.durable.delegation import Delegated, DelegationView
from harness_core.kernel.durable.ports import ModelCall
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.kernel.fold import RunState
from harness_core.logs import HarnessLog
from harness_core.models.policy import ModelPolicy
from harness_core.models.transport_model import TransportModel
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.after_run import AfterRunCase, AfterRunStepContract
from harness_core.testing.contracts.durable_ports import (
    CapabilityCheckContract,
    DurableModelPortContract,
    GateCase,
    ImportedHistoryContract,
    InputRendererContract,
    PostRunStepContract,
    PreRunGateContract,
    PreRunStepContract,
    RunPresenterContract,
    RunRestoreContract,
    ending,
)
from harness_core.testing.contracts.durable_turns import (
    ChildResultShaperContract,
    ContextHostContract,
    DelegationToolsContract,
    LoopDirectorContract,
    ResponseReviewContract,
    RoutePortsCase,
    RoutePortsContract,
    TurnShaperContract,
)
from harness_core.testing.durable import ScriptedModelPort, provider_events
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events
from tests.unit.agent_loop_lib.hooks.middleware.builtin.test_skill_learning import (
    _FakeSkillManager,  # pyright: ignore[reportPrivateUsage]
)

from . import world as w
from .test_model_policy_wiring import (
    AI_MODELS,
    TWO,
    _Services,  # pyright: ignore[reportPrivateUsage]
    _spec_model,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from pydantic import JsonValue

    from app.agent_loop_lib.core.types import AgentResult
    from harness_core.driver import AssembledRuntime
    from harness_core.kernel.durable.ports import CapabilityCheck, RunRestore

LOG = HarnessLog.named("tests.pipeshub_port_contracts")
QUERY = "find the bug"
T = TypeVar("T")


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def _runtime(chat_mode: str = "deep", *, mcp: bool = False, **extension: Any) -> AssembledRuntime:  # noqa: ANN401
    """``mcp``: the agent's MCP server is granted too (a live manifest the capability check observes)."""
    agent = w.agent_doc()
    tool_ids = [f"jira@{w.JIRA}/search_issues", f"jira@{w.JIRA}/create_issue"]
    if mcp:
        tool_ids.append(f"mcp@{w.MCP}/list_prs")
    spec = w.pinned_spec(tool_ids=tool_ids, kb=["kb-1"], mcp=[w.MCP] if mcp else [])
    spec = spec.model_copy(update={"budgets": spec.budgets.model_copy(update={"max_children": 16})})
    if extension:
        spec = spec.model_copy(update={"extensions": {"x.pipeshub": {**(spec.extensions or {})["x.pipeshub"], **extension}}})
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(spec, query=QUERY, chat_mode=chat_mode, current_time="2026-10-01T12:00:00Z")
    return await PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port)).build(claim)


async def _journaled_state(runtime: AssembledRuntime) -> RunState:
    """The state after the first attempt's pre-run commit: every step's result as its slot."""
    slots: dict[str, JsonValue] = {}
    for step in runtime.pre_run:
        slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run(QUERY)
    return RunState(run_id="run-1", slots=slots)


class Runtimes(Generic[T]):
    """``fresh()`` hands out one assembled runtime's port per call, as a re-claim would build it."""

    def __init__(self, runtimes: list[AssembledRuntime], take: Callable[[AssembledRuntime], T], state: RunState) -> None:
        self._runtimes = runtimes
        self._take = take
        self._state = state

    def fresh(self) -> T:
        return self._take(self._runtimes.pop())

    def state(self) -> RunState:
        return self._state

    def call(self) -> ModelCall:
        return ModelCall(messages=[UserMessage(content=QUERY)], slots={}, input_index=0)


async def _runtimes(take: Callable[[AssembledRuntime], T], *, count: int = 3, **extension: Any) -> Runtimes[T]:  # noqa: ANN401
    runtimes = [await _runtime(**extension) for _ in range(count + 1)]
    return Runtimes(runtimes, take, await _journaled_state(runtimes.pop()))


def _typed(kind: type[T]) -> Callable[[object], T]:
    def take(value: object) -> T:
        assert isinstance(value, kind), f"the assembler wired {type(value).__name__}, not {kind.__name__}"
        return value

    return take


# ---- DP1 DurableModelPort: ClassifiedModelPort over the streaming port, as resolve_model builds it ----


class ClassifiedPorts:
    def _over(self, transport: RecordedTransport) -> ClassifiedModelPort:
        return ClassifiedModelPort(StreamingModelPort(TransportModel(transport), provider="openai", transport="direct"))

    def port(self, reply: Reply) -> ClassifiedModelPort:
        return self._over(RecordedTransport(provider_events(reply)))

    def failing(self) -> ClassifiedModelPort:
        return self._over(RecordedTransport(fail=RuntimeError("Error code: 500 - internal server error")))

    def rate_limited(self) -> ClassifiedModelPort:
        return self._over(RecordedTransport(fail=RuntimeError("Error code: 429 - Rate limit exceeded")))


class TestClassifiedModelPort(DurableModelPortContract):
    @pytest.fixture
    def model_ports(self) -> ClassifiedPorts:  # type: ignore[override]
        return ClassifiedPorts()


# ---- DP3 PreRunStep ----


class TestGoalStep(PreRunStepContract):
    @pytest.fixture
    async def pre_run_step(self) -> GoalStep:
        (step,) = [s for s in (await _runtime("quick")).pre_run if isinstance(s, GoalStep)]
        return step


class TestSubAgentGoalStep(PreRunStepContract):
    @pytest.fixture
    async def pre_run_step(self) -> SubAgentGoalStep:
        runtime = await _runtime(subAgent={"role": "tickets", "toolNames": ["jira__search_issues"]})
        (step,) = [s for s in runtime.pre_run if isinstance(s, SubAgentGoalStep)]
        return step


class TestCompositionStep(PreRunStepContract):
    @pytest.fixture
    def pre_run_step(self) -> CompositionStep:
        composition = Composition(delegates=("jira",), top_level=("search",), claims={"jira": ["search_issues"]})
        return CompositionStep(composition, LOG)


class TestUnavailableItemsStep(PreRunStepContract):
    @pytest.fixture
    def pre_run_step(self) -> UnavailableItemsStep:
        return UnavailableItemsStep((UnavailableItem("toolset", "inst-42", "Jira"), UnavailableItem("mcp", "mcp-9", "GitHub")))


# ---- DP4 PostRunStep ----


class TestSkillLearningStep(PostRunStepContract):
    @pytest.fixture
    def post_run_step(self) -> SkillLearningStep:
        from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import (
            SkillLearning,
        )

        async def spawn(goal: str) -> AgentResult:
            return agent_result(ending("answered"), goal)

        return SkillLearningStep(
            SkillLearning(_FakeSkillManager(), spawn),  # type: ignore[arg-type]
            goal="deploy the service",
            run_id="run-1",
            session_id=None,
        )


# ---- DP5 PreRunGate, DP6 CapabilityCheck ----


class TestClarificationGate(PreRunGateContract):
    @pytest.fixture
    def gate_case(self) -> GateCase:
        return GateCase(
            ClarificationGate(),
            asks={GOAL_SLOT: {"description": QUERY, "clarifyingQuestions": [{"question": "Which service?"}]}},
            proceeds={GOAL_SLOT: {"description": QUERY, "clarifyingQuestions": []}},
        )


class TestAssembledCapabilityCheck(CapabilityCheckContract):
    """The assembler's ``_Observation``: what the attempt's tool sources really built."""

    @pytest.fixture
    async def capability_check(self) -> CapabilityCheck:
        check = (await _runtime("quick", mcp=True)).capability_check
        assert check is not None
        return check


# ---- DP7 RunPresenter, DP8 RunRestore, DP9 InputRenderer, DP12 ImportedHistory ----


def _context(state: dict[str, Any] | None = None) -> Any:  # noqa: ANN401 - the legacy AgentContext's shape
    return SimpleNamespace(tool_state=state if state is not None else {}, root_agent_spec=None, conversation_id=None, run_id="run-1")


class TestPipesHubPresenter(RunPresenterContract):
    @pytest.fixture
    async def presenters(self) -> Callable[[], PipesHubPresenter]:  # type: ignore[override]
        return (await _runtimes(lambda r: _typed(PipesHubPresenter)(r.presenter), count=2, chat_mode="quick")).fresh


class CitationRestores:
    def fresh(self) -> CitationJournal:
        return CitationJournal(_context({"record_id_shortener": RecordIdShortener()}), log=LOG)

    async def live(self) -> tuple[CitationJournal, list[NewEvent]]:
        mapper, shortener = CitationRefMapper(), RecordIdShortener()
        state: dict[str, Any] = {"citation_ref_mapper": mapper, "record_id_shortener": shortener}
        live = CitationJournal(_context(state), log=LOG)
        events: list[NewEvent] = []
        for call_id, url, record in (("c1", "https://kb.example/r1", "record-aaaa"), ("c2", "https://kb.example/r2", "record-bbbb")):
            _ = mapper.get_or_create_ref(url)
            _ = shortener.get_or_create_short_id(record)
            state["virtual_record_id_to_result"] = {**state.get("virtual_record_id_to_result", {}), record: {"id": record}}
            state["known_record_ids"] = {*state.get("known_record_ids", set()), record}
            delta = live.delta(call_id)
            assert delta is not None
            events.append(NewEvent(CITATIONS_EVENT, delta, critical=False))
        return live, events

    def state(self, restore: RunRestore) -> JsonValue:
        assert isinstance(restore, CitationJournal)
        state = restore._context.tool_state  # pyright: ignore[reportPrivateUsage]
        mapper = state.get("citation_ref_mapper")
        rebuilt: dict[str, Any] = {
            "refs": dict(mapper.ref_to_url) if mapper is not None else {},
            "shortIds": [list(e) for e in state["record_id_shortener"].entries()],
            "records": state.get("virtual_record_id_to_result") or {},
            "known": sorted(state.get("known_record_ids") or ()),
        }
        return rebuilt


class TestCitationJournal(RunRestoreContract):
    @pytest.fixture
    def restores(self) -> CitationRestores:  # type: ignore[override]
        return CitationRestores()


class TestPipesHubInputRenderer(InputRendererContract):
    @pytest.fixture
    async def renderers(self) -> Callable[[], PipesHubInputRenderer]:  # type: ignore[override]
        return (await _runtimes(lambda r: _typed(PipesHubInputRenderer)(r.inputs), count=2)).fresh


class TestLegacyHistory(ImportedHistoryContract):
    @pytest.fixture
    async def imported_history(self) -> LegacyHistory:
        return _typed(LegacyHistory)((await _runtime("quick")).history)


# ---- DP10 TurnShaper, DP11 ResponseReview, DP15 ContextHost ----


class TestPipesHubTurnShaper(TurnShaperContract):
    @pytest.fixture
    async def shapers(self) -> Runtimes[PipesHubTurnShaper]:  # type: ignore[override]
        return await _runtimes(lambda r: _typed(PipesHubTurnShaper)(r.shaper), chat_mode="quick")


class TestPipesHubResponseReview(ResponseReviewContract):
    @pytest.fixture
    async def reviews(self) -> Runtimes[PipesHubResponseReview]:  # type: ignore[override]
        return await _runtimes(lambda r: _typed(PipesHubResponseReview)(r.review), chat_mode="quick")


class TestPipesHubTurnShaperBands(ContextHostContract):
    """``PipesHubTurnShaper`` is the compiler's ``ContextHost`` too (P4.1)."""

    @pytest.fixture
    async def hosts(self) -> Runtimes[PipesHubTurnShaper]:  # type: ignore[override]
        return await _runtimes(lambda r: _typed(PipesHubTurnShaper)(r.shaper), chat_mode="quick")


# ---- DP13 ChildResultShaper, DP14 LoopDirector, DP16 DelegationTools ----


class TestSubAgentResult(ChildResultShaperContract):
    @pytest.fixture
    async def child_result(self) -> SubAgentResult:
        return _typed(SubAgentResult)((await _runtime(subAgent={"role": "r", "toolNames": []})).child_result)


class DeepDirectors(Runtimes[DeepDirector]):
    @override
    def state(self) -> RunState:
        goal: dict[str, JsonValue] = {"description": QUERY, "loopKind": "orchestrator", "mode": "deep", "clarifyingQuestions": []}
        return RunState(run_id="run-1", slots={GOAL_SLOT: goal})


class TestDeepDirector(LoopDirectorContract):
    @pytest.fixture
    async def directors(self) -> DeepDirectors:  # type: ignore[override]
        runtimes = [await _runtime() for _ in range(3)]
        return DeepDirectors(runtimes, lambda r: _typed(DeepDirector)(r.director), RunState(run_id="run-1"))


SPAWN = ToolCall(id="c1", name="spawn_agent", arguments={"role": "tickets", "goal": "List the open tickets", "reasoning": "x", "task_id": "tickets"})


class DeepDelegations(Runtimes[DeepDelegation]):
    def ready(self) -> tuple[Delegated, DelegationView]:
        pending = Delegated("t2.0", SPAWN, 2, "pending")
        approved = {DEEP_PHASE_SLOT: DeepPhase(phase="programmatic", approved=True).slot()}
        return pending, DelegationView((pending,), (), approved)


class TestDeepDelegation(DelegationToolsContract):
    @pytest.fixture
    async def delegations(self) -> DeepDelegations:  # type: ignore[override]
        runtimes = [await _runtime() for _ in range(3)]
        return DeepDelegations(runtimes, lambda r: _typed(DeepDelegation)(r.delegation), RunState(run_id="run-1"))



CODER_SPAWN = ToolCall(id="c1", name="spawn_agent", arguments={"role": "coder", "goal": "Fix the failing test", "task_id": "fix"})


class HarnessAgentRunners(DeepDelegations):
    """CM-02: deep mode's spawn_agent with the run's engine pinned, a coder spawn ready."""

    @override
    def ready(self) -> tuple[Delegated, DelegationView]:
        pending = Delegated("t2.0", CODER_SPAWN, 2, "pending")
        approved = {DEEP_PHASE_SLOT: DeepPhase(phase="programmatic", approved=True).slot()}
        return pending, DelegationView((pending,), (), approved)


def _runner(runtime: AssembledRuntime) -> Any:  # noqa: ANN401
    from app.harness.assembly.code_task import CombinedDelegation
    from app.harness.assembly.harness_agent_runner import HarnessAgentRunner

    combined = _typed(CombinedDelegation)(runtime.delegation)
    return next(p for p in combined._parts if isinstance(p, HarnessAgentRunner))  # pyright: ignore[reportPrivateUsage]


class TestHarnessAgentRunner(DelegationToolsContract):
    @pytest.fixture
    async def delegations(self) -> HarnessAgentRunners:  # type: ignore[override]
        vectors = Path(__file__).resolve().parents[6] / "contracts/harness/vectors/engine-profiles.json"
        engine = json.loads(vectors.read_text())["release"][0]
        runtimes = [await _runtime(codeTask={"profile": engine}) for _ in range(3)]
        return HarnessAgentRunners(runtimes, _runner, RunState(run_id="run-1"))  # type: ignore[arg-type]


class CodeTaskDelegations:
    """CTL-6's ``code_task`` alone, and beside deep mode's ``spawn_agent`` (``CombinedDelegation``)."""

    def __init__(self, *, combined: bool) -> None:
        self._combined = combined

    def fresh(self) -> Any:  # noqa: ANN401
        from app.harness.assembly.code_task import (
            CodeTaskDelegation,
            CombinedDelegation,
        )
        from harness_core.kernel.durable.delegation import (
            DelegatingTool,
            NamedDelegation,
            goal_tool,
        )

        code_task = CodeTaskDelegation()
        if not self._combined:
            return code_task
        helper = NamedDelegation({"helper": DelegatingTool(goal_tool("helper", "A loop helper."))})
        return CombinedDelegation(helper, code_task)

    def ready(self) -> tuple[Delegated, DelegationView]:
        call = ToolCall(id="c1", name="code_task", arguments={"goal": "write report.md"})
        pending = Delegated("t1.0", call, 1, "pending")
        return pending, DelegationView((pending,))


class TestCodeTaskDelegation(DelegationToolsContract):
    @pytest.fixture
    def delegations(self) -> CodeTaskDelegations:  # type: ignore[override]
        return CodeTaskDelegations(combined=False)


class TestCombinedDelegation(DelegationToolsContract):
    @pytest.fixture
    def delegations(self) -> CodeTaskDelegations:  # type: ignore[override]
        return CodeTaskDelegations(combined=True)


# ---- DP18 RoutePorts ----


class TestConfiguredRoutePorts(RoutePortsContract):
    @pytest.fixture
    def route_ports(self) -> RoutePortsCase:
        unreachable = {"provider": "anthropic", "model": "claude-x", "on": ["5xx"]}
        spec_model = _spec_model(*TWO, unreachable)
        run = w.claim(w.pinned_spec(tool_ids=[]))

        async def resolve(routed: RunContext, services: object) -> ResolvedModel:
            port = ScriptedModelPort(ScriptedModel(ModelScript(id="fallback", turns=[Turn(reply=Reply(text="ok"))])))
            return ResolvedModel(raw_llm=None, provider=routed.model.provider, is_multimodal=False, context_length=None, is_reasoning=False, port=port)

        ports = ConfiguredRoutePorts(
            RunContext.from_claim(run), _Services(AI_MODELS), tuple(spec_model.policy),  # type: ignore[arg-type]
            resolve=resolve, meter=lambda model, _routed: model,
        )
        routes = ModelPolicy.from_spec(spec_model).routes
        return RoutePortsCase(ports, reachable=routes[1], unreachable=routes[2])




class _Kept:
    def __init__(self) -> None:
        self.items: list[list[str]] = []

    async def deliver(self, items: Sequence[str]) -> bool:
        self.items.append(list(items))
        return True


class TestFollowupsStepAfterRun(AfterRunStepContract):
    """UX5's suggested next questions: reserved on the run's ledger while leased, called and settled after the end."""

    @pytest.fixture
    async def after_run_case(self) -> AfterRunCase:
        account = RunAccountRef("org-dp19", "run-dp19")
        ledger = InMemoryBudgetLedger(ManualClock())
        _ = await ledger.open_root(account, Cap(None, 10_000_000))

        def metered(inner: RecordedTransport) -> MeteredTransport:
            return MeteredTransport(inner, accounts=ledger, account=account, pricing_model="gpt-6-luna", ids=SeededIds(0))

        kept = _Kept()
        log = HarnessLog.named("tests.dp19")
        reply = json.dumps(["Compare it with last week", "Who approved the deploy?"])
        step = FollowupsStep(
            metered(RecordedTransport([reply_events(reply)])),
            question="When did the deploy finish?", run_id="run-dp19", fence=1, bus=None, sink=kept, log=log,
        )
        failing = FollowupsStep(
            metered(RecordedTransport(fail=RuntimeError("provider down"))),
            question="When did the deploy finish?", run_id="run-dp19", fence=1, bus=None, sink=_Kept(), log=log,
        )

        async def held() -> int:
            return (await ledger.remaining(account)).reserved.tokens

        async def charged() -> int:
            return (await ledger.remaining(account)).committed.tokens

        return AfterRunCase(step, failing, lambda: len(kept.items), held, charged)

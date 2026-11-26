"""WP-97 APPROVAL-EXPLAIN: a research run's approval card with no reason, replayed.

The run's Codex child (``1f62c71b…``) saved ``report.md`` with ``ph tools call artifacts__save_artifact``. The org's
approval section on the stack said ``writes: ask``, so the pinned governance asked, as an org rule does on any sink. The
pin reaches the engine through ``LiveNarrowedApprovals``, which said ``ask`` but not why; ``ToolRulesPolicy`` above it
then found nothing to pass on, the engine default allowed the own-space write (``effects.default_own_space``), and
seq 2577 ``interaction.requested`` went to the journal alone. These tests fail on the code of that run.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from app.control_plane.runtime.live_layer import LiveLayer, folded_live_layer
from app.control_plane.runtime.live_policy import LiveNarrowedApprovals
from app.control_plane.testing.contracts import ORG
from app.control_plane.testing.memory import InMemoryLiveLayerSource, live_approvals
from app.harness.approvals import engine_guarded, governed
from app.harness.labels import SessionSourceLedger
from app.harness.tool_rules import ToolRulesPolicy
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.clock import SystemClock
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.events.interaction_explained import (
    InteractionExplainedData,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import EffectRunner, RequestEffectLog, ToolExecution
from harness_core.effects.ask_reason import (
    REASON_MISSING_LOG,
    card_reason,
    plain_reason,
)
from harness_core.effects.flow import GrantingLedger
from harness_core.effects.policy import (
    ApprovalRules,
    LayeredApprovalPolicy,
    parse_rules,
)
from harness_core.effects.types import ExplainsAsk, ToolEffect, args_digest
from harness_core.engines.policy import engine_default_approvals
from harness_core.interactions.approval_request import (
    UnexplainedApproval,
    approval_request,
    check_explained,
    unexplained,
)
from harness_core.interactions.hold import BridgeApprovalHold, approval_interaction_id
from harness_core.interactions.journal import JournalInteractionBridge
from harness_core.interactions.memory import InMemoryInteractionWakeup
from harness_core.interactions.ports import RunBinding
from harness_core.interactions.run_view import ClaimInteractionView
from harness_core.journal.memory import InMemoryJournal
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.effect_log import CountingTool, context
from harness_core.testing.contracts.tool_catalog import OfferedTool, ToolCatalogContract
from harness_core.testing.engines.durable import EngineWorld
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted
from tests.unit.harness_core.effects.test_runner import make_call

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy
    from harness_core.security.labels import SourceLedger

LOG: Final = HarnessLog.named("tests.wp97")
AGENT: Final = "agent-1"
SAVE_ID: Final = "pipeshub@artifacts/save_artifact"
# Run 10's call: a write to the person's own PipesHub space (gateway log ``egress=none``).
SAVE: Final = ToolEffect(SAVE_ID, "write", False, SinkDescriptor(egress="none", args_leave_boundary=False))
SAVE_CALL: Final = ToolCall(id="c-2576", name="artifacts__save_artifact", arguments={"name": "report.md", "content": "# R"})
# The recorded org section, as the run's pin resolved it: the seeded tool rule beside the org's own ``writes: ask``.
RECORDED_ORG: Final = {"writes": "ask", "tools": {"mcp@f6cf3be4/create_ticket": "ask"}}
BINDING: Final = RunBinding("org-1", "scope-1", "run-1")


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


def _layer(*, stale: bool = False, org: JsonValue | None = None) -> LiveLayer:
    source = InMemoryLiveLayerSource()
    source.put(ORG, 5, *((live_approvals(org, None),) if org is not None else ()))
    return replace(folded_live_layer(source.documents[ORG]), stale=stale)


async def _live(pin: ApprovalPolicy, layer: LiveLayer) -> LiveNarrowedApprovals:
    async def current() -> LiveLayer:
        return layer

    policy = LiveNarrowedApprovals(pin, current, agent_key=AGENT, log=LOG)
    await policy.refresh()
    return policy


def _org_layer(rules: JsonValue) -> LayeredApprovalPolicy:
    """The org's layer as the recorded run's pin held it (a pin names whose rule each layer is)."""
    return LayeredApprovalPolicy((parse_rules(rules),), ("org",))


def _pin() -> LayeredApprovalPolicy:
    org_policy = _org_layer(RECORDED_ORG)
    return governed(org_policy, _profile(), ApprovalRules())


def _profile():  # noqa: ANN202
    from harness_core.testing.engines.durable import engine_profile

    return engine_profile()


@dataclass
class _Registry:
    """Main's per-tool rules see no PipesHub toolset path here: they allow, as they did for run 10's save."""

    def names(self) -> list[str]:
        return []

    def path_for_name(self, name: str) -> str | None:
        return None


@dataclass
class _Context:
    tool_state: dict[str, object] = field(default_factory=dict)
    is_service_account: bool = False


def _engine_chain(governance: ApprovalPolicy) -> ApprovalPolicy:
    ledger = GrantingLedger(SessionSourceLedger())
    return engine_guarded(
        engine_default_approvals(),
        ToolRulesPolicy(governance, _Context(), _Registry()),  # type: ignore[arg-type]
        rules=flow_rules(LOG),
        ledger=ledger,
        run_id="run-1",
        log=LOG,
    )


def _valid(reason: Mapping[str, JsonValue]) -> None:
    _ = validate_contract(InteractionExplainedData, {"interactionId": "ix.approve.k1", **reason})


# WP97-1: run 10's policy chain


async def test_wp97_1_run10_s_own_space_save_under_the_org_s_writes_ask_says_the_org_asks() -> None:
    policy = _engine_chain(await _live(_pin(), _layer()))
    assert policy(SAVE_CALL, SAVE) == "ask", "an org's writes: ask holds every sink, the person's own space too (UX4)"
    assert isinstance(policy, ExplainsAsk)
    reason = policy.ask_reason(SAVE_CALL, SAVE)
    assert reason is not None, "run 10: the pin's reason stopped at the live wrapper"
    assert (reason["rule"], reason["layer"], reason["toolId"], reason["conversationGrant"]) == (
        "writes", "org", SAVE_ID, False,
    )
    _valid(reason)


async def test_wp97_1_without_the_org_rule_the_own_space_save_runs_unasked() -> None:
    org_policy = _org_layer({"tools": RECORDED_ORG["tools"]})
    policy = _engine_chain(await _live(governed(org_policy, _profile(), ApprovalRules()), _layer()))
    assert policy(SAVE_CALL, SAVE) == "allow", "#4313: a write to the person's own space runs when no rule asks"


# WP97-2: the live layer names its own rules


async def test_wp97_2_a_live_tightening_names_the_org_and_a_stale_layer_names_the_unread_rules() -> None:
    tightened = await _live(LayeredApprovalPolicy(), _layer(org={"tools": {SAVE_ID: "ask"}}))
    assert tightened.ask_reason(SAVE_CALL, SAVE) == {"rule": "tool", "layer": "org", "toolId": SAVE_ID, "conversationGrant": False}
    stale = await _live(LayeredApprovalPolicy(), _layer(stale=True))
    reason = stale.ask_reason(SAVE_CALL, SAVE)
    assert reason == {"rule": "writes", "layer": "org", "toolId": SAVE_ID, "conversationGrant": False}
    _valid(reason)
    allowed = await _live(LayeredApprovalPolicy(), _layer())
    assert allowed(SAVE_CALL, SAVE) == "allow" and allowed.ask_reason(SAVE_CALL, SAVE) is None


# WP97-3: every policy that can ask says why (the contract that would have caught run 10)

_ROOTS: Final = ("harness_core", "app", "harness_engines")
# The protocol itself, and a test fixture outside production code.
_NOT_POLICIES: Final = {("harness_core/effects/types.py", "ApprovalPolicy"), ("harness_core/testing/engines/durable.py", "_Fixed")}


def _policy_classes() -> list[tuple[str, str, bool]]:
    base = Path(__file__).resolve().parents[3]
    found: list[tuple[str, str, bool]] = []
    for root in _ROOTS:
        for path in sorted((base / root).rglob("*.py")):
            rel = path.relative_to(base).as_posix()
            if "/contracts/generated/" in rel:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if not isinstance(node, ast.ClassDef):
                    continue
                methods = {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
                call = methods.get("__call__")
                if call is None or call.returns is None or "ApprovalDecision" not in ast.unparse(call.returns):
                    continue
                if (rel, node.name) not in _NOT_POLICIES:
                    found.append((rel, node.name, "ask_reason" in methods))
    return found


def test_wp97_3_every_approval_policy_in_the_product_explains_its_ask() -> None:
    classes = _policy_classes()
    assert ("app/control_plane/runtime/live_policy.py", "LiveNarrowedApprovals", True) in classes
    assert len(classes) >= 7, classes
    assert [(path, name) for path, name, explains in classes if not explains] == []


# WP97-4: the runner never hands a hold an ask without a reason


class _AsksSilently:
    """A policy that asks and names no rule: the shape of the live wrapper before this package."""

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "ask"


@dataclass
class _RecordingHold:
    reasons: list[Mapping[str, JsonValue]] = field(default_factory=list)

    def explain(self, call: object, reason: Mapping[str, JsonValue]) -> None:
        self.reasons.append(reason)

    async def hold(self, call: object) -> None:
        return None


async def test_wp97_4_a_policy_that_asks_without_a_reason_still_gives_the_card_one(caplog: pytest.LogCaptureFixture) -> None:
    hold = _RecordingHold()
    runner = EffectRunner(RequestEffectLog(), _AsksSilently(), hold)  # type: ignore[arg-type]
    call = make_call(SAVE, "inv-348", {"name": "report.md"})
    with caplog.at_level(logging.WARNING):
        result = await runner.run(call, context(call), CountingTool())
    assert result.disposition == "held"
    # R38-AUTHZ: the reason names the call's arguments digest, which a person's decline covers.
    assert hold.reasons == [{**plain_reason(SAVE), "argsDigest": args_digest({"name": "report.md"})}]
    assert REASON_MISSING_LOG in caplog.text and SAVE_ID in caplog.text
    _valid(hold.reasons[0])


def test_wp97_4_the_plain_reason_is_the_policy_s_own_when_it_gives_one() -> None:
    explained = {"rule": "writes", "layer": "org", "toolId": SAVE_ID, "conversationGrant": False}

    class _Explains(_AsksSilently):
        def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue]:
            return explained

    assert card_reason(_Explains(), SAVE_CALL, SAVE, LOG) == {**explained, "argsDigest": args_digest(SAVE_CALL.arguments)}
    assert plain_reason(ToolEffect("web@default/fetch", "read", True, SinkDescriptor(egress="external", args_leave_boundary=True)))[
        "rule"
    ] == "effect_class"


# WP97-5: the commit seam refuses a request without its reason


def test_wp97_5_one_constructor_puts_the_reason_first_and_the_seam_checks_it() -> None:
    reason = {"rule": "writes", "layer": "org", "toolId": SAVE_ID, "conversationGrant": False}
    ix = "ix.approve.k76b4f6ade2c36a82401200512c288de4fc860367"
    events = approval_request(ix, "sha256:fed8", reason)
    assert [e.type for e in events] == ["interaction.explained", "interaction.requested"]
    assert events[0].critical is False and events[0].data["interactionId"] == ix
    check_explained(events, explains=True)
    # Run 10's command (seq 2576..2577): the approval line and the request, no reason.
    line = NewEvent("x.pipeshub.engine.progress", {"kind": "approval", "data": {"interactionId": ix}}, critical=False)
    run10 = (line, events[1])
    assert unexplained(run10) == [ix]
    with pytest.raises(UnexplainedApproval):
        check_explained(run10, explains=True)
    # A journal that predates reasons gets the request alone, as before UX1.
    assert approval_request(ix, "sha256:fed8", None) == (events[1],)
    check_explained(run10, explains=False)
    # Connect cards and an unknown effect's hold are not approvals of a call.
    connect = NewEvent("interaction.requested", {"interactionId": "ix.connect.inv-1", "kind": "connect"})
    unknown = NewEvent("interaction.requested", {"interactionId": "ix.unknown.inv-1", "kind": "approval"})
    check_explained((connect, unknown), explains=True)


async def _claimed() -> tuple[InMemoryJournal, object]:
    journal = InMemoryJournal()
    journal.admit(BINDING.run_id, org_id=BINDING.tenant, session_id=BINDING.scope, spec=minimal_run_spec(), input_text="hi")
    claim = await journal.claim(BINDING.run_id, worker_id="w")
    assert claim is not None
    return journal, claim


async def test_wp97_5_an_engine_s_hold_journals_the_reason_with_the_request_in_one_command() -> None:
    journal, claim = await _claimed()
    view = ClaimInteractionView(journal, claim)  # type: ignore[arg-type]
    bridge = JournalInteractionBridge(view, InMemoryInteractionWakeup(), SystemClock(), poll_s=0.01)
    runner = EffectRunner(RequestEffectLog(), _AsksSilently(), BridgeApprovalHold(bridge, BINDING, hold_s=0.02))  # type: ignore[arg-type]
    call = make_call(SAVE, "inv-348", {"name": "report.md"})
    assert (await runner.run(call, context(call), CountingTool())).disposition == "held"
    ix = approval_interaction_id(call.key)
    [(_, types)] = [c for c in journal.commits(BINDING.run_id) if "interaction.requested" in c[1]]
    assert types == ["interaction.explained", "interaction.requested"]
    [explained] = [e for e in journal.events(BINDING.scope) if e.type == "interaction.explained"]
    assert explained.data == {**plain_reason(SAVE), "argsDigest": args_digest({"name": "report.md"}), "interactionId": ix}


async def test_wp97_5_the_seam_refuses_a_bare_approval_request_and_journals_nothing() -> None:
    journal, claim = await _claimed()
    view = ClaimInteractionView(journal, claim)  # type: ignore[arg-type]
    bare = NewEvent("interaction.requested", {"interactionId": "ix.approve.k1", "kind": "approval", "bindingDigest": "sha256:x"})
    with pytest.raises(UnexplainedApproval):
        await view.append([bare])
    assert [e for e in journal.events(BINDING.scope) if e.type.startswith("interaction.")] == []


# WP97-6: run 10 end to end on an engine run (the kernel's presenting view and the Tool Gateway's runner)

SAVE_TOOL: Final = OfferedTool("save_artifact", SAVE)


def _engine_world(governance: ApprovalPolicy) -> EngineWorld:
    def guard(default: ApprovalPolicy, ledger: SourceLedger, run_id: str) -> ApprovalPolicy:
        assert isinstance(ledger, GrantingLedger)
        return engine_guarded(
            default, ToolRulesPolicy(governance, _Context(), _Registry()),  # type: ignore[arg-type]
            rules=flow_rules(LOG), ledger=ledger, run_id=run_id, log=LOG,
        )

    async def save(call: ToolCall, ctx: object) -> ToolExecution:
        return ToolExecution(status="ok", content="saved")

    return EngineWorld(
        tools=(SAVE_TOOL,),
        handlers={"save_artifact": save},  # type: ignore[dict-item]
        ledger=lambda: GrantingLedger(SessionSourceLedger(tool_names={SAVE_ID: "save_artifact"})),
        guard=guard,
    )


async def test_wp97_6_run10_replayed_the_engine_s_save_card_says_the_org_asks() -> None:
    w = _engine_world(await _live(_pin(), _layer()))
    w.admit("run-10", 'tool save_artifact {"name": "report.md", "content": "# Onyx vs PipesHub"}\nsay waiting')
    assert (await w.attempt("run-10")).status == "suspended"
    events = w.events("run-10")
    [request] = [e for e in events if e.type == "interaction.requested"]
    [reason] = [e for e in events if e.type == "interaction.explained"]
    assert reason.data["interactionId"] == request.data["interactionId"]
    assert (reason.data["rule"], reason.data["layer"], reason.data["toolId"]) == ("writes", "org", SAVE_ID)
    assert reason.data["conversationGrant"] is False, "an org's own rule is never answered for the conversation"
    assert reason.seq + 1 == request.seq, "the reason and the request are one command"


# WP97-7: the durable loop journals a plain reason for a silent ask


def _post_world() -> World:
    world = World()
    world.admit()
    return world


async def test_wp97_7_a_loop_run_s_silent_ask_is_journaled_with_the_plain_reason() -> None:
    world = _post_world()
    _, port = scripted(reply("", ("post_message", {"channel": "sales", "text": "hi"})), reply("posted"))  # type: ignore[arg-type]
    done = await world.run(world.kernel(port, approval=_AsksSilently()))  # type: ignore[arg-type]
    assert done.status == "suspended"
    [request] = world.events("interaction.requested")
    [reason] = world.events("interaction.explained")
    assert reason.data["interactionId"] == request.data["interactionId"]
    assert reason.data["rule"] == "writes" and reason.data["conversationGrant"] is False
    assert any(
        types.count("interaction.explained") == 1 and types.index("interaction.explained") < types.index("interaction.requested")
        for _, types in world.journal.commits(RUN)
        if "interaction.requested" in types
    )


async def test_wp97_7_an_older_loop_journal_still_gets_the_request_alone() -> None:
    world = World(journal_accepts=frozenset(), journal_refuses=frozenset({"interaction.explained"}))
    world.admit()
    _, port = scripted(reply("", ("post_message", {"channel": "sales", "text": "hi"})), reply("posted"))  # type: ignore[arg-type]
    done = await world.run(world.kernel(port, approval=_AsksSilently()))  # type: ignore[arg-type]
    assert done.status == "suspended"
    assert world.events("interaction.requested") and world.events("interaction.explained") == []


# WP97-8: a save of the run's own deliverable stays in the run (ADR-RUN-01 D1 ``Deliverable.owns``)


@dataclass
class _Catalog:
    """The artifacts toolset as the gateway serves it: ``save_artifact`` writes to the person's own space."""

    executed: list[str] = field(default_factory=list)

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        return SAVE if call.name == "artifacts__save_artifact" else None

    def schemas(self) -> None:
        return None

    async def execute(self, call: ToolCall, ctx: object) -> ToolExecution:
        self.executed.append(str(call.arguments.get("name")))
        return ToolExecution(status="ok", content="saved")


async def test_wp97_8_run10_s_second_save_of_report_md_asks_nobody_and_makes_no_second_artifact() -> None:
    from app.harness.engines.deliverable_saves import DeliverableSaves
    from harness_core.engines.outcome.deliverable import Deliverable

    inner = _Catalog()
    catalog = DeliverableSaves(inner, Deliverable("file", "report.md"), LOG)
    policy = _engine_chain(await _live(_pin(), _layer()))
    effect = catalog.describe_call(SAVE_CALL)
    assert effect is not None and (effect.effect, effect.sink.egress) == ("workspace", "none")
    assert policy(SAVE_CALL, effect) == "allow", "the org's writes: ask is about writes outside the run"
    done = await catalog.execute(SAVE_CALL, context(make_call(SAVE)))
    assert done.status == "ok" and "/workspace/out/report.md" in str(done.content)
    assert inner.executed == []
    # Any other file is still the person's own-space write, which the org's rule holds.
    other = ToolCall(id="c-2", name="artifacts__save_artifact", arguments={"name": "notes.md", "content": "x"})
    assert catalog.describe_call(other) == SAVE and policy(other, SAVE) == "ask"
    await catalog.execute(other, context(make_call(SAVE)))
    assert inner.executed == ["notes.md"]
    # A run whose deliverable is an answer owns no file.
    answer = DeliverableSaves(_Catalog(), Deliverable("answer"), LOG)
    assert answer.describe_call(SAVE_CALL) == SAVE


class _DeliverableCatalogs:
    """``DeliverableSaves`` over the in-memory catalog: a decorator that changes only the deliverable's saves."""

    def __init__(self) -> None:
        from tests.unit.harness_core.invoker.catalogs import MemoryCatalogs

        self._inner = MemoryCatalogs()

    @property
    def runs(self) -> list[str]:
        return self._inner.runs

    def build(self, offered: object, grant: object) -> object:
        from app.harness.engines.deliverable_saves import DeliverableSaves
        from harness_core.engines.outcome.deliverable import Deliverable

        return DeliverableSaves(self._inner.build(offered, grant), Deliverable("file", "report.md"), LOG)  # type: ignore[arg-type]


class TestDeliverableSavesCatalog(ToolCatalogContract):
    """WP97-8: ``DeliverableSaves`` passes the ``ToolCatalog`` contract (TC-1..TC-8) for every call it forwards."""

    @pytest.fixture
    def harness(self) -> _DeliverableCatalogs:  # type: ignore[override]
        return _DeliverableCatalogs()


def test_wp97_5_only_a_journal_that_takes_every_reason_refuses_a_bare_request() -> None:
    from harness_core.interactions.approval_request import explains_every_ask
    from harness_core.journal.types import APPROVAL_REASONS, APPROVAL_RULE_LAYERS

    assert explains_every_ask(lambda feature: True)
    # An older Node takes reasons but not layers (QA6-F6): an org rule's card goes alone there, by design.
    assert not explains_every_ask(lambda feature: feature == APPROVAL_REASONS)
    assert not explains_every_ask(lambda feature: feature in (APPROVAL_REASONS, APPROVAL_RULE_LAYERS))

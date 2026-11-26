"""CPD-6 ``LiveNarrowedApprovals``: NTF-10 at the wrapper (never looser than the pin), NTF-06/07 and NTF-09 at the
unit level (a tightening reaches the next decision; a stale layer closes writes and keeps reads open), and that the
effect runner and the policies that wrap it refresh it before deciding."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final, get_args

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.control_plane.runtime.live_layer import LiveLayer, folded_live_layer
from app.control_plane.runtime.live_policy import LiveNarrowedApprovals
from app.control_plane.testing.contracts import ORG, change_event
from app.control_plane.testing.memory import (
    InMemoryLiveLayerCache,
    InMemoryLiveLayerSource,
    live_approvals,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import EffectRunner, RequestEffectLog
from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    DefaultAskPolicy,
    FlowGuardedPolicy,
    FlowRule,
)
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    ApprovalRules,
    LayeredApprovalPolicy,
)
from harness_core.effects.types import (
    EffectKind,
    EffectResult,
    RefreshingPolicy,
    ToolEffect,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.effect_log import CountingTool, context
from tests.unit.harness_core.effects.test_runner import make_call

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalDecision

LOG: Final = HarnessLog.named("tests.cpd6.live_policy")
RANK: Final[dict[str, int]] = {"allow": 0, "ask": 1, "deny": 2}
KINDS: Final = get_args(EffectKind)
TOOLS: Final = ("jira@inst-42/create_issue", "jira@inst-42/search_issues", "web@default/web_search")
CALL: Final = ToolCall(id="c", name="t", arguments={})
SINK: Final = SinkDescriptor(egress="external", args_leave_boundary=True)
OWN: Final = SinkDescriptor(egress="none", args_leave_boundary=False)
WRITE: Final = ToolEffect(TOOLS[0], "write", False, SINK)
READ: Final = ToolEffect(TOOLS[1], "read", True, SINK)

decisions = st.sampled_from(("allow", "ask", "deny"))
rules = st.builds(
    ApprovalRules,
    classes=st.dictionaries(st.sampled_from(KINDS), decisions, max_size=len(KINDS)),
    tools=st.dictionaries(st.sampled_from(TOOLS), decisions, max_size=len(TOOLS)),
)
pins = st.lists(rules, max_size=3).map(lambda ls: LayeredApprovalPolicy(tuple(ls)))
wire_rules = st.fixed_dictionaries(
    {},
    optional={
        "writes": decisions,
        "classes": st.dictionaries(st.sampled_from(KINDS), decisions, max_size=len(KINDS)),
        "tools": st.dictionaries(st.sampled_from(TOOLS), decisions, max_size=len(TOOLS)),
    },
)
live_layers = st.lists(st.tuples(wire_rules, st.sampled_from((None, "a7", "a8"))), max_size=4)


def _layer(members: list[tuple[JsonValue, str | None]], *, stale: bool = False) -> LiveLayer:
    source = InMemoryLiveLayerSource()
    source.put(ORG, 5, *(live_approvals(approvals, agent) for approvals, agent in members))
    return replace(folded_live_layer(source.documents[ORG]), stale=stale)


async def _narrowed(pin: LayeredApprovalPolicy, layer: LiveLayer, agent_key: str | None) -> LiveNarrowedApprovals:
    async def live() -> LiveLayer:
        return layer

    policy = LiveNarrowedApprovals(pin, live, agent_key=agent_key, log=LOG)
    await policy.refresh()
    return policy


def _effects() -> list[ToolEffect]:
    return [ToolEffect(tool, kind, False, sink) for tool in TOOLS for kind in KINDS for sink in (SINK, OWN)]


@settings(max_examples=300, deadline=None, database=None)
@given(pin=pins, live=live_layers, agent=st.sampled_from((None, "a7", "a9")), stale=st.booleans())
async def test_ntf10_the_live_layer_never_loosens_a_pinned_decision(
    pin: LayeredApprovalPolicy, live: list[tuple[JsonValue, str | None]], agent: str | None, stale: bool
) -> None:
    policy = await _narrowed(pin, _layer(live, stale=stale), agent)
    for effect in _effects():
        assert RANK[policy(CALL, effect)] >= RANK[pin(CALL, effect)], effect


@settings(max_examples=100, deadline=None, database=None)
@given(pin=pins, agent=st.sampled_from((None, "a7")))
async def test_ntf10_an_empty_fresh_live_layer_decides_as_the_pin(pin: LayeredApprovalPolicy, agent: str | None) -> None:
    policy = await _narrowed(pin, _layer([]), agent)
    for effect in _effects():
        assert policy(CALL, effect) == pin(CALL, effect)


@settings(max_examples=200, deadline=None, database=None)
@given(live=live_layers, agent=st.sampled_from((None, "a7", "a8")))
async def test_the_live_decision_is_the_strictest_of_the_org_s_and_the_agent_s_rules(
    live: list[tuple[JsonValue, str | None]], agent: str | None
) -> None:
    from harness_core.effects.policy import parse_rules

    policy = await _narrowed(LayeredApprovalPolicy(), _layer(live), agent)
    applying = LayeredApprovalPolicy(tuple(parse_rules(r) for r, key in live if key is None or key == agent))
    for effect in _effects():
        assert policy(CALL, effect) == applying(CALL, effect), effect


async def test_ntf09_a_stale_live_layer_asks_before_a_write_and_keeps_a_read_open() -> None:
    allow = LayeredApprovalPolicy()
    stale = await _narrowed(allow, _layer([], stale=True), None)

    assert stale(CALL, WRITE) == "ask"
    assert stale(CALL, ToolEffect(TOOLS[0], "execute", False, OWN)) == "ask"
    assert stale(CALL, READ) == "allow"

    denied = await _narrowed(LayeredApprovalPolicy((ApprovalRules(classes={"write": "deny"}),)), _layer([], stale=True), None)
    assert denied(CALL, WRITE) == "deny"


def test_ntf09_before_its_first_refresh_the_policy_is_closed_for_writes() -> None:
    async def live() -> LiveLayer:
        raise AssertionError("not read by a decision")

    policy = LiveNarrowedApprovals(LayeredApprovalPolicy(), live, agent_key=None, log=LOG)

    assert (policy(CALL, WRITE), policy(CALL, READ)) == ("ask", "allow")


async def test_ntf09_a_refresh_that_fails_keeps_the_rules_and_closes_writes(caplog: pytest.LogCaptureFixture) -> None:
    answers: list[LiveLayer | Exception] = [_layer([({"tools": {TOOLS[1]: "deny"}}, None)]), RuntimeError("boom")]

    async def live() -> LiveLayer:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    policy = LiveNarrowedApprovals(LayeredApprovalPolicy(), live, agent_key=None, log=LOG)
    await policy.refresh()
    assert (policy(CALL, WRITE), policy(CALL, READ)) == ("allow", "deny")

    with caplog.at_level("WARNING"):
        await policy.refresh()

    assert (policy(CALL, WRITE), policy(CALL, READ)) == ("ask", "deny")
    assert "live_layer_unavailable" in caplog.text
    assert "boom" not in caplog.text


class _Explains:
    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "deny" if effect.effect == "execute" else "allow"

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        return ("pinned_rule", "the pin says no")


async def test_a_live_denial_is_the_policy_s_and_a_pinned_one_keeps_its_own_explanation() -> None:
    policy = LiveNarrowedApprovals(
        _Explains(), lambda: _const(_layer([({"writes": "deny"}, None)])), agent_key=None, log=LOG
    )
    await policy.refresh()

    assert policy(CALL, WRITE) == "deny"
    assert policy.denial(CALL, WRITE) == ("policy", "denied by policy")
    assert policy.denial(CALL, ToolEffect(TOOLS[0], "execute", False, SINK)) == ("pinned_rule", "the pin says no")


async def _const(layer: LiveLayer) -> LiveLayer:
    return layer


async def _run(runner: EffectRunner, effect: ToolEffect, invocation: str, tool: CountingTool) -> EffectResult:
    call = make_call(effect, invocation, {"title": invocation})
    return await runner.run(call, context(call), tool)


class _NoRead:
    def source_records(self, run_id: str) -> frozenset[str] | None:
        return frozenset()


@pytest.mark.parametrize("wrapped", ["bare", "flow_guarded", "engine_default"])
async def test_ntf06_a_tightening_reaches_the_runner_s_next_decision_through_every_wrapper(wrapped: str) -> None:
    """The effect runner refreshes its policy before it decides; the flow guard and the engine's default forward the
    refresh to the policy they wrap."""
    clock = ManualClock()
    cache = InMemoryLiveLayerCache(clock)
    cache.source.put(ORG, 1)
    live = LiveNarrowedApprovals(LayeredApprovalPolicy(), lambda: cache.current(ORG), agent_key=None, log=LOG)
    ledger = _NoRead()
    policy = {
        "bare": live,
        "flow_guarded": FlowGuardedPolicy(live, FlowRule("ask"), ledger, "run-1"),  # type: ignore[arg-type]
        "engine_default": DefaultAskPolicy(
            LayeredApprovalPolicy(), live, ENGINE_WRITES_RULE, ledger, "run-1"  # type: ignore[arg-type]
        ),
    }[wrapped]
    assert isinstance(policy, RefreshingPolicy)
    runner = EffectRunner(RequestEffectLog(), policy)
    tool = CountingTool()
    read = ToolEffect(TOOLS[1], "read", True, OWN)

    first = await _run(runner, read, "inv-1", tool)
    cache.source.put(ORG, 2, live_approvals({"classes": {"read": "deny"}}))
    cache.on_change(change_event(ORG, 2))
    second = await _run(runner, read, "inv-2", tool)

    assert (first.disposition, second.disposition, tool.calls) == ("executed", "denied", 1)


async def test_ntf07_without_an_event_the_tightening_reaches_the_runner_by_the_time_to_live() -> None:
    clock = ManualClock()
    cache = InMemoryLiveLayerCache(clock)
    cache.source.put(ORG, 1)
    live = LiveNarrowedApprovals(ENGINE_APPROVALS, lambda: cache.current(ORG), agent_key=None, log=LOG)
    runner = EffectRunner(RequestEffectLog(), live)
    tool = CountingTool()
    read = ToolEffect(TOOLS[1], "read", True, OWN)

    _ = await _run(runner, read, "inv-1", tool)
    cache.source.put(ORG, 2, live_approvals({"classes": {"read": "deny"}}))
    _ = clock.advance(29.0)
    before = await _run(runner, read, "inv-2", tool)
    _ = clock.advance(1.0)
    after = await _run(runner, read, "inv-3", tool)

    assert (before.disposition, after.disposition) == ("executed", "denied")


async def test_ntf12_ten_thousand_decisions_on_a_fresh_layer_make_no_request() -> None:
    import time

    clock = ManualClock()
    cache = InMemoryLiveLayerCache(clock)
    tools = {f"jira@inst-42/tool_{n}": ("ask" if n % 2 else "deny") for n in range(500)}
    cache.source.put(ORG, 1, live_approvals({"tools": tools}))
    live = LiveNarrowedApprovals(LayeredApprovalPolicy(), lambda: cache.current(ORG), agent_key=None, log=LOG)
    await live.refresh()
    effects = [ToolEffect(tool, "write", False, SINK) for tool in tools]
    samples: list[int] = []

    for n in range(10_000):
        started = time.perf_counter_ns()
        await live.refresh()
        decision = live(CALL, effects[n % 500])
        samples.append(time.perf_counter_ns() - started)
        assert decision == tools[effects[n % 500].tool_id]

    assert cache.source.calls == [(ORG, None)]
    samples.sort()
    print(f"NTF-12 refresh+decide p50={samples[5_000] / 1e3:.1f}us p95={samples[9_500] / 1e3:.1f}us")

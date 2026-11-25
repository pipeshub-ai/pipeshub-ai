"""TGW-18, APR-07, POL-22 (QA-10): properties of approvals and the flow guard over generated configurations.

Every example runs on the product's composition for its path (``paths.py``), so the properties hold for what a run
actually does, not for a model of it:

* deny-monotonicity: adding an approval layer never loosens a decision, and a layer that denies the call is absorbing;
* grants (UX1, UX2) answer only the flow rule and the engine default: never past an org or agent rule, never a deny;
* the Rule of Two: after a private or unknown read, an external write is never allowed unless a person allowed that
  tool and destination for the conversation;
* taint sticks: what the session ledger says a run has read survives a resume (any chunking, a new ledger), only
  rises with later events, and passes to a child at the spawn;
* an engine run's own calls are never decided more loosely than the loop's (TEST-STRATEGY's ``caller=code``).

The Hypothesis profile sets the examples per property: 200 on PR (``ci``), 5,000 nightly (``--hypothesis-profile=nightly``).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Final, get_args

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.harness.approvals import approval_policy
from app.harness.labels import SessionSourceLedger, label_from_wire
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    DefaultAskPolicy,
    sensitivity_of,
)
from harness_core.effects.policy import ENGINE_APPROVALS, stricter
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.engines.types import ProfileApprovals
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.source_ledger import Stream
from tests.unit.contracts_harness.support import CONTRACTS
from tests.unit.harness.engines.test_source_ledger import PipesHubLedgerWorld, answered
from tests.unit.harness.policy.paths import (
    PATHS,
    RUN,
    TOOL_NAME,
    UNCLASSIFIED,
    Context,
    Grant,
    PathName,
    Scenario,
    probe,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalDecision, EffectKind
    from harness_core.journal.types import JournalEvent

PROPERTY: Final = settings(
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
LOG: Final = HarnessLog.named("tests.qa10.properties")
RANK: Final[dict[str, int]] = {"allow": 0, "ask": 1, "deny": 2}
TOOL_ID: Final = tool_id_for(TOOL_NAME)
LEVELS: Final = st.sampled_from(["allow", "ask", "deny"])
KINDS: Final = st.sampled_from(["read", "workspace", "write", "execute"])
SECTIONS: Final = ("default", "agents")
EXTERNAL_WRITES: Final = ("write/external", "execute/external", UNCLASSIFIED)
TOOLS: Final = st.sampled_from(
    [
        "read/none",
        "read/external",
        "workspace/none",
        *(f"{k}/{e}" for k in ("write", "execute") for e in ("none", "self", "internal_audience", "external")),
        UNCLASSIFIED,
    ]
)
CONTEXTS: Final = st.sampled_from(get_args(Context))
TAINTED: Final = st.sampled_from(["private", "unknown", "after-write", "child-private", "child-unlabelled"])
GRANTS: Final = st.sampled_from(get_args(Grant))
FLOW_ON: Final = st.sampled_from(
    [
        {},
        {"HARNESS_FLOW_GUARD_LEVEL": "deny"},
        {"HARNESS_FLOW_GUARD_LEVEL": "sometimes"},
        {"HARNESS_FLOW_GUARD_ABOVE": "secret"},
        {"HARNESS_FLOW_GUARD": "true", "HARNESS_FLOW_GUARD_LEVEL": "ask"},
    ]
)
DEPLOYMENTS: Final = st.one_of(
    FLOW_ON,
    st.sampled_from(
        [
            {"HARNESS_FLOW_GUARD": "false"},
            {"HARNESS_FLOW_GUARD_ABOVE": "private"},
            {"HARNESS_FLOW_GUARD_LEVEL": "deny", "HARNESS_FLOW_GUARD_ABOVE": "private"},
        ]
    ),
)
LAYERS: Final = st.fixed_dictionaries(
    {},
    optional={
        "writes": LEVELS,
        "classes": st.dictionaries(KINDS, LEVELS, max_size=2),
        "tools": st.dictionaries(st.sampled_from([TOOL_ID, tool_id_for("acme_other")]), LEVELS, max_size=1),
        "flow": st.fixed_dictionaries(
            {"level": st.sampled_from(["off", "ask", "deny"])},
            optional={"above": st.sampled_from(["public", "private"])},
        ),
    },
)


def _placed(section: str, layer: dict[str, Any]) -> dict[str, Any]:
    return {"default": layer, "agents": {"agent-1": layer}}[section]


@st.composite
def documents(draw: st.DrawFn, *, without: str | None = None) -> dict[str, Any] | None:
    sections = [s for s in SECTIONS if s != without]
    chosen = draw(st.lists(st.sampled_from(sections), unique=True, max_size=len(sections)))
    if not chosen and without is None and draw(st.booleans()):
        return None
    return {section: _placed(section, draw(LAYERS)) for section in chosen}


DURABLE_PATHS: Final = st.sampled_from(["durable", "engine"])


def decide(path: PathName, scenario: Scenario) -> str:
    return asyncio.run(PATHS[path].decide(scenario))


def rank(verdict: str) -> int:
    return RANK[verdict.split("/", 1)[0]]


def org_rules(document: JsonValue, tool: str) -> ApprovalDecision:
    """What the org's and agents' class and tool rules alone decide (P2.6c), with no flow rule and no grant."""
    subject = probe(tool)
    return approval_policy(document, org_id="org-1", agent_key="agent-1", log=LOG)(subject.call, subject.effect)


# APR-07: deny-monotonicity and strictest-of over layers.


@PROPERTY
@given(
    path=DURABLE_PATHS,
    section=st.sampled_from(SECTIONS),
    data=st.data(),
    layer=LAYERS,
    tool=TOOLS,
    context=CONTEXTS,
    deployment=DEPLOYMENTS,
    grant=GRANTS,
)
def test_apr07_adding_a_layer_never_loosens_a_decision(
    path: PathName, section: str, data: st.DataObject, layer: dict[str, Any], tool: str, context: Context,
    deployment: dict[str, str], grant: Grant,
) -> None:
    document = data.draw(documents(without=section))
    before = Scenario(tool=tool, context=context, document=document, deployment=deployment, grant=grant)
    after = Scenario(
        tool=tool, context=context, document={**(document or {}), section: _placed(section, layer)},
        deployment=deployment, grant=grant,
    )
    assert rank(decide(path, after)) >= rank(decide(path, before))


@PROPERTY
@given(
    path=st.sampled_from(["durable", "engine"]),
    data=st.data(),
    section=st.sampled_from(SECTIONS),
    by_tool=st.booleans(),
    tool=TOOLS,
    context=CONTEXTS,
    deployment=DEPLOYMENTS,
    grant=GRANTS,
)
def test_apr07_a_layer_that_denies_the_call_is_absorbing(
    path: PathName, data: st.DataObject, section: str, by_tool: bool, tool: str, context: Context,
    deployment: dict[str, str], grant: Grant,
) -> None:
    kind = probe(tool).effect.effect
    deny: dict[str, Any] = {"tools": {TOOL_ID: "deny"}} if by_tool else {"classes": {kind: "deny"}}
    document = {**(data.draw(documents(without=section)) or {}), section: _placed(section, deny)}
    scenario = Scenario(tool=tool, context=context, document=document, deployment=deployment, grant=grant)
    assert decide(path, scenario) == "deny/policy"


# TGW-18: grants never widen past an org rule; a deny is never granted.


@PROPERTY
@given(path=DURABLE_PATHS, document=documents(), tool=TOOLS, context=CONTEXTS, deployment=DEPLOYMENTS, grant=GRANTS)
def test_tgw18_a_grant_never_widens_past_an_org_rule(
    path: PathName, document: dict[str, Any] | None, tool: str, context: Context, deployment: dict[str, str],
    grant: Grant,
) -> None:
    granted = decide(path, Scenario(tool=tool, context=context, document=document, deployment=deployment, grant=grant))
    ungranted = decide(path, Scenario(tool=tool, context=context, document=document, deployment=deployment))
    assert rank(granted) >= RANK[org_rules(document, tool)]
    assert rank(granted) <= rank(ungranted)
    if ungranted.startswith("deny"):
        assert granted == ungranted
    if rank(granted) != rank(ungranted):
        assert (ungranted.startswith("ask"), granted, grant) == (True, "allow", "same")
    if grant != "same":
        assert granted == ungranted


def _profile_denying(kind: EffectKind) -> ProfileApprovals:
    return ProfileApprovals(write="deny") if kind == "write" else ProfileApprovals(execute="deny")


DENIALS: Final = st.sampled_from(["default", "agent-tool", "deployment-flow", "default-flow", "profile"])


@PROPERTY
@given(
    path=DURABLE_PATHS,
    denial=DENIALS,
    document=documents(),
    tool=st.sampled_from(EXTERNAL_WRITES),
    context=TAINTED,
    grant=GRANTS,
)
def test_tgw18_no_grant_answers_a_deny(
    path: PathName, denial: str, document: dict[str, Any] | None, tool: str, context: Context, grant: Grant
) -> None:
    _ = assume(denial != "profile" or path == "engine")
    kind = probe(tool).effect.effect
    deny_layer: dict[str, dict[str, Any]] = {
        "default": {"default": {"classes": {kind: "deny"}}},
        "agent-tool": {"agents": {"agent-1": {"tools": {TOOL_ID: "deny"}}}},
        "default-flow": {"default": {"flow": {"level": "deny"}}},
    }
    scenario = Scenario(
        tool=tool,
        context=context,
        document={**(document or {}), **deny_layer.get(denial, {})},
        deployment={"HARNESS_FLOW_GUARD_LEVEL": "deny"} if denial == "deployment-flow" else {},
        profile=_profile_denying(kind) if denial == "profile" else ProfileApprovals(),
        grant=grant,
    )
    assert decide(path, scenario).startswith("deny/")


# TGW-18: the Rule of Two.


@PROPERTY
@given(
    path=DURABLE_PATHS,
    document=documents(),
    tool=st.sampled_from(EXTERNAL_WRITES),
    context=TAINTED,
    deployment=FLOW_ON,
    grant=GRANTS,
)
def test_tgw18_rule_of_two_no_external_write_after_a_private_read_without_a_person(
    path: PathName, document: dict[str, Any] | None, tool: str, context: Context, deployment: dict[str, str],
    grant: Grant,
) -> None:
    verdict = decide(path, Scenario(tool=tool, context=context, document=document, deployment=deployment, grant=grant))
    assert verdict != "allow" or grant == "same"


@PROPERTY
@given(tool=st.sampled_from(EXTERNAL_WRITES), context=st.sampled_from(["private", "unknown"]), deployment=FLOW_ON)
def test_tgw18_rule_of_two_on_todays_loop_refuses_instead(tool: str, context: Context, deployment: dict[str, str]) -> None:
    assert decide("legacy", Scenario(tool=tool, context=context, deployment=deployment)) == "deny/approval_unavailable"


@PROPERTY
@given(document=documents(), tool=TOOLS, context=CONTEXTS, deployment=DEPLOYMENTS, grant=GRANTS)
def test_tgw18_an_engine_runs_own_calls_are_never_looser_than_the_loops(
    document: dict[str, Any] | None, tool: str, context: Context, deployment: dict[str, str], grant: Grant
) -> None:
    scenario = Scenario(tool=tool, context=context, document=document, deployment=deployment, grant=grant)
    assert rank(decide("engine", scenario)) >= rank(decide("durable", scenario))


# TGW-18: taint stickiness across fold and resume, and into a child at the spawn.

Step = tuple[str, tuple[str, ...]]
STEPS: Final = st.lists(
    st.one_of(
        st.tuples(st.just("recorded"), st.lists(st.sampled_from(["rec-1", "rec-2", "rec-3"]), min_size=1, max_size=2).map(tuple)),
        st.tuples(st.just("unrecorded"), st.just(())),
        st.tuples(st.just("refused"), st.just(())),
        st.tuples(st.just("earlier"), st.lists(st.sampled_from(["rec-4", "rec-5"]), max_size=2).map(tuple)),
    ),
    max_size=6,
)
_SENS: Final[dict[str, int]] = {"public": 0, "private": 1, "unknown": 2}


def _stream(steps: list[Step], *, parent: str | None = None) -> tuple[Stream, list[int]]:
    """The session's stream after each step, and where each step ends. ``earlier``: a finished turn whose answer
    recorded the given sources."""
    stream, world = Stream(), PipesHubLedgerWorld()
    ends: list[int] = []
    stream.admitted(RUN, parent=parent)
    for i, (kind, records) in enumerate(steps):
        invocation = f"inv-{i}"
        if kind == "recorded":
            world.recorded(stream, RUN, invocation, list(records))
        elif kind == "unrecorded":
            world.unrecorded(stream, RUN, invocation)
        elif kind == "refused":
            world.refused(stream, RUN, invocation)
        else:
            earlier = f"run-earlier-{i}"
            stream.admitted(earlier)
            sources: list[JsonValue] = [{"kind": "indexed", "recordId": r} for r in records]
            answered(stream, earlier, {"complete": True, "sources": sources})
        ends.append(len(stream.events))
    return stream, ends


def _sensitivity(events: list[JournalEvent], *, chunks: list[int] | None = None) -> str:
    ledger = SessionSourceLedger()
    cuts: list[int] = chunks or []
    for start, end in zip([0, *cuts], [*cuts, len(events)], strict=True):
        ledger.apply(events[start:end])
    return sensitivity_of(ledger.source_records(RUN))


@PROPERTY
@given(steps=STEPS, cuts=st.lists(st.integers(min_value=0, max_value=40), max_size=4))
def test_tgw18_taint_survives_any_resume(steps: list[Step], cuts: list[int]) -> None:
    events = _stream(steps)[0].events
    chunks = sorted({min(c, len(events)) for c in cuts})
    whole = _sensitivity(events)
    assert _sensitivity(events, chunks=chunks) == whole
    replayed = SessionSourceLedger()
    replayed.apply(events)
    replayed.apply(events)
    assert sensitivity_of(replayed.source_records(RUN)) == whole


@PROPERTY
@given(steps=STEPS)
def test_tgw18_taint_only_rises_as_the_run_goes_on(steps: list[Step]) -> None:
    stream, ends = _stream(steps)
    seen = [_sensitivity(stream.events[:end]) for end in ends]
    assert all(_SENS[a] <= _SENS[b] for a, b in zip(seen, seen[1:], strict=False))


@PROPERTY
@given(parent_steps=STEPS, child_steps=STEPS)
def test_tgw18_a_child_inherits_its_parents_taint_at_the_spawn(parent_steps: list[Step], child_steps: list[Step]) -> None:
    parent = SessionSourceLedger()
    parent.apply(_stream(parent_steps)[0].events)
    passed = label_from_wire(parent.context_label(RUN))
    child = SessionSourceLedger(inherited=passed)
    child.apply(_stream(child_steps, parent="run-parent")[0].events)
    at_spawn = sensitivity_of(parent.source_records(RUN))
    assert _SENS[sensitivity_of(child.source_records(RUN))] >= _SENS[at_spawn]


# POL-22: approvals through the effective policy of every shared vector; the engine composes as strictest-of.

CLASSES: Final[tuple[EffectKind, ...]] = ("read", "workspace", "write", "execute")
UNRULED: Final = tool_id_for("unruled")
VECTORS: Final[dict[str, Any]] = json.loads((CONTRACTS / "vectors" / "approvals.json").read_text(encoding="utf-8"))


def _effect(kind: EffectKind, tool_id: str, egress: str = "external") -> ToolEffect:
    sink = SinkDescriptor(egress=egress, args_leave_boundary=egress == "external")  # type: ignore[arg-type]
    return ToolEffect(tool_id, kind, kind in ("read", "workspace"), sink)


@pytest.mark.parametrize("case", VECTORS["documents"], ids=lambda case: case["id"])
def test_pol22_every_vector_decides_each_call_as_its_effective_policy(case: dict[str, Any]) -> None:
    policy = approval_policy(case["document"], org_id=case["orgId"], agent_key=case["agentKey"], log=LOG)
    effective = case["effective"]
    call = ToolCall(id="c", name="t", arguments={})
    for kind in CLASSES:
        for tool in (*effective["tools"], UNRULED):
            expected = stricter(effective["classes"][kind], effective["tools"].get(tool, "allow"))
            assert policy(call, _effect(kind, tool)) == expected, (kind, tool)


class _NoLedger:
    def source_records(self, run_id: str) -> frozenset[str] | None:
        return frozenset()

    def apply(self, events: object) -> None:
        return None


@pytest.mark.parametrize("case", VECTORS["documents"], ids=lambda case: case["id"])
def test_pol22_an_engine_run_decides_as_the_strictest_of_its_default_and_the_document_s_rules(case: dict[str, Any]) -> None:
    governance = approval_policy(case["document"], org_id=case["orgId"], agent_key=case["agentKey"], log=LOG)
    composed = DefaultAskPolicy(governance, ENGINE_APPROVALS, ENGINE_WRITES_RULE, _NoLedger(), RUN)  # type: ignore[arg-type]
    call = ToolCall(id="c", name="t", arguments={})
    for kind in CLASSES:
        for tool in (*case["effective"]["tools"], UNRULED):
            effect = _effect(kind, tool)
            assert composed(call, effect) == stricter(governance(call, effect), ENGINE_APPROVALS(call, effect))

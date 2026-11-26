"""SEC1 FL-1..FL-4, FL-10: the flow rule as a narrowing layer of the approval policy."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, get_args

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    FLOW_DENIAL,
    FlowGuardedPolicy,
    FlowRule,
    Sensitivity,
    sensitivity_of,
)
from harness_core.effects.policy import ApprovalRules, LayeredApprovalPolicy
from harness_core.effects.types import (
    POLICY_DENIAL,
    EffectKind,
    ToolEffect,
    allow_all,
    tool_id_for,
)
from harness_core.kernel.core.messages import ToolCall

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.effects.types import ApprovalDecision
    from harness_core.journal.types import JournalEvent

CALL = ToolCall(id="c1", name="post")
SINKS = ("none", "self", "internal_audience", "external")


def effect(kind: EffectKind = "write", egress: str = "external") -> ToolEffect:
    return ToolEffect(
        tool_id=tool_id_for("post", source="mcp"),
        effect=kind,
        idempotent=False,
        sink=SinkDescriptor(egress=egress, args_leave_boundary=egress == "external"),  # type: ignore[arg-type]
    )


@dataclass
class Ledger:
    records: frozenset[str] | None = frozenset()
    applied: list[JournalEvent] = field(default_factory=list["JournalEvent"])

    def apply(self, events: Sequence[JournalEvent]) -> None:
        self.applied.extend(events)

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self.records


@pytest.mark.parametrize(
    ("records", "expected"), [(frozenset(), "public"), (frozenset({"rec-1"}), "private"), (None, "unknown")]
)
def test_fl2_sensitivity_from_the_ledgers_answer(records: frozenset[str] | None, expected: Sensitivity) -> None:
    assert sensitivity_of(records) == expected


RANK = {"public": 0, "private": 1, "unknown": 2}


@pytest.mark.parametrize(
    ("sensitivity", "kind", "egress", "level", "above"),
    list(itertools.product(get_args(Sensitivity), get_args(EffectKind), SINKS, ("ask", "deny"), ("public", "private"))),
)
def test_fl1_only_a_call_that_sends_data_out_above_the_threshold_is_narrowed(
    sensitivity: Sensitivity, kind: EffectKind, egress: str, level: str, above: str
) -> None:
    rule = FlowRule(level, above)  # type: ignore[arg-type]
    # FG2: a read whose request goes outside (fetch_url) as much as an external write.
    narrowed = kind != "workspace" and egress == "external" and RANK[sensitivity] > RANK[above]
    assert rule.decide(sensitivity, effect(kind, egress)) == (level if narrowed else "allow")


def test_fl10_unknown_content_is_above_either_threshold() -> None:
    for above in ("public", "private"):
        assert FlowRule("deny", above).decide("unknown", effect()) == "deny"  # type: ignore[arg-type]


def _base(decision: ApprovalDecision) -> LayeredApprovalPolicy:
    return LayeredApprovalPolicy((ApprovalRules(classes={"write": decision}),))


@pytest.mark.parametrize("base", ["allow", "ask", "deny"])
@pytest.mark.parametrize("records", [frozenset(), frozenset({"rec-1"}), None])
@pytest.mark.parametrize("level", ["ask", "deny"])
def test_fl3_the_guard_only_narrows(base: ApprovalDecision, records: frozenset[str] | None, level: str) -> None:
    guarded = FlowGuardedPolicy(_base(base), FlowRule(level), Ledger(records), "run-1")  # type: ignore[arg-type]
    flow = level if records != frozenset() else "allow"
    order = ["allow", "ask", "deny"]
    expected = order[max(order.index(base), order.index(flow))]
    assert guarded(CALL, effect()) == expected
    assert order.index(guarded(CALL, effect())) >= order.index(base)


def test_fl3_a_read_that_stays_inside_is_never_asked_about_and_never_consults_the_ledger() -> None:
    class Untouchable(Ledger):
        def source_records(self, run_id: str) -> frozenset[str] | None:
            raise AssertionError("a read must not consult the ledger")

    guarded = FlowGuardedPolicy(allow_all, FlowRule("deny"), Untouchable(None), "run-1")
    assert guarded(CALL, effect("read", "self")) == "allow"
    assert guarded(CALL, effect("read", "none")) == "allow"
    assert guarded(CALL, effect("workspace", "self")) == "allow"


def test_fl4_a_flow_deny_names_its_rule_and_a_policy_deny_keeps_its_own() -> None:
    by_flow = FlowGuardedPolicy(allow_all, FlowRule("deny"), Ledger(frozenset({"rec-1"})), "run-1")
    assert by_flow(CALL, effect()) == "deny"
    assert by_flow.denial(CALL, effect()) == FLOW_DENIAL
    by_policy = FlowGuardedPolicy(_base("deny"), FlowRule("deny"), Ledger(frozenset({"rec-1"})), "run-1")
    assert by_policy.denial(CALL, effect()) == POLICY_DENIAL


def test_fl4_the_ledger_is_asked_about_the_guarded_run() -> None:
    asked: list[str] = []

    class Recording(Ledger):
        def source_records(self, run_id: str) -> frozenset[str] | None:
            asked.append(run_id)
            return frozenset()

    FlowGuardedPolicy(allow_all, FlowRule("ask"), Recording(), "run-7")(CALL, effect())
    assert asked == ["run-7"]

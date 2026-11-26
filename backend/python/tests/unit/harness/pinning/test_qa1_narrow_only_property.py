"""QA-1 NTF-10, IX-NTF-PIN-4: ``pin ⊓ live ⊑ pin`` for the dimensions the live layer narrows on the worker.

Whatever the live layer holds, folding it onto a run's pinned approval layers decides every call at least as
strictly as the pin alone, and the flow rules of the fold decide at least as strictly for every state of what
the run has read. The operations are today's: ``LayeredApprovalPolicy.narrowed`` and the strictest-of flow
rules behind ``flow_guarded`` (02-lld §3.2: a ceiling is a meet in its dimension's order).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, get_args

from hypothesis import given, settings
from hypothesis import strategies as st

from app.harness.approvals import flow_guarded
from app.harness.unpinned import flow_rules
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.policy import ApprovalRules, LayeredApprovalPolicy, LayerFlow
from harness_core.effects.types import EffectKind, ToolEffect
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from harness_core.effects.types import ApprovalDecision

LOG = HarnessLog.named("tests.qa1.narrow_only")
RANK: Final[dict[str, int]] = {"allow": 0, "ask": 1, "deny": 2}
KINDS: Final = get_args(EffectKind)
TOOLS: Final = ("jira@inst-42/create_issue", "jira@inst-42/search_issues", "web@default/web_search")
CALL: Final = ToolCall(id="c", name="t", arguments={})
SINK: Final = SinkDescriptor(egress="external", args_leave_boundary=True)
# What the run has read: nothing, a named record, or an unknown context.
READ_STATES: Final = (frozenset[str](), frozenset({"rec-1"}), None)

decisions = st.sampled_from(("allow", "ask", "deny"))
layer_flows = st.none() | st.builds(LayerFlow, st.sampled_from(("off", "ask", "deny")), st.sampled_from(("public", "private")))
layers = st.builds(
    ApprovalRules,
    classes=st.dictionaries(st.sampled_from(KINDS), decisions, max_size=len(KINDS)),
    tools=st.dictionaries(st.sampled_from(TOOLS), decisions, max_size=len(TOOLS)),
    flow=layer_flows,
)
pins = st.lists(layers, max_size=4).map(lambda ls: LayeredApprovalPolicy(tuple(ls)))


def _effects() -> list[ToolEffect]:
    return [ToolEffect(tool, kind, False, SINK) for tool in TOOLS for kind in KINDS]


class _Read:
    def __init__(self, records: frozenset[str] | None) -> None:
        self._records = records

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self._records


def _allow(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
    return "allow"


def _guarded(flows: tuple[LayerFlow, ...], records: frozenset[str] | None, effect: ToolEffect) -> str:
    return flow_guarded(_allow, rules=flow_rules(LOG, flows), ledger=_Read(records), run_id="r", log=LOG)(CALL, effect)  # type: ignore[arg-type]


@settings(max_examples=300, deadline=None, database=None)
@given(pin=pins, live=layers)
def test_ntf10_the_live_layer_never_loosens_an_approval(pin: LayeredApprovalPolicy, live: ApprovalRules) -> None:
    folded = pin.narrowed(live)
    for effect in _effects():
        assert RANK[folded(CALL, effect)] >= RANK[pin(CALL, effect)], (effect, pin, live)


@settings(max_examples=300, deadline=None, database=None)
@given(pin=pins, live=layers)
def test_ntf10_the_live_layer_never_loosens_a_flow_rule(pin: LayeredApprovalPolicy, live: ApprovalRules) -> None:
    folded = pin.narrowed(live)
    for records in READ_STATES:
        for effect in _effects():
            pinned = _guarded(pin.flows, records, effect)
            assert RANK[_guarded(folded.flows, records, effect)] >= RANK[pinned], (records, effect, pin, live)

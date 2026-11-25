"""CPD3-40, CPD3-41 (POL-22 through the order): approvals as the ``approval-rules`` order. For every approvals
document vector, the fold of the run's layers through the order is the vector's ``effective`` policy, and the
fold decides every class and tool as ``LayeredApprovalPolicy`` over the same layers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import pytest

from app.harness.approvals import approval_policy
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.policy import stricter
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.policy import DimensionPolicyResolver, ScopedLayer, order_of

from .support import APPROVAL_VECTORS

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.policy import ApprovalRules
    from harness_core.effects.types import ApprovalDecision, EffectKind

LOG: Final = HarnessLog.named("tests.cpd3.approvals")
CLASSES: Final[tuple[EffectKind, ...]] = ("read", "workspace", "write", "execute")
UNRULED: Final = tool_id_for("unruled")
ORDER: Final = order_of({"kind": "approval-rules"})
LAYER_KINDS: Final = ("deployment", "org", "deployment", "agent")


def _wire(rules: ApprovalRules) -> dict[str, JsonValue]:
    wire: dict[str, JsonValue] = {"classes": dict(rules.classes), "tools": dict(rules.tools)}
    if rules.flow is not None:
        wire["flow"] = {"level": rules.flow.level, "above": rules.flow.above}
    return wire


def _layers(case: dict[str, Any]) -> list[dict[str, JsonValue]]:
    policy = approval_policy(case["document"], org_id=case["orgId"], agent_key=case["agentKey"], log=LOG)
    return [_wire(rules) for rules in policy.layers]


def _fold(layers: list[dict[str, JsonValue]]) -> object:
    ceiling: object = ORDER.lift({})
    for layer in layers:
        ceiling = ORDER.meet(ceiling, ORDER.lift(layer))
    return ceiling


@pytest.mark.parametrize("case", APPROVAL_VECTORS["documents"], ids=lambda case: case["id"])
def test_cpd3_40_the_fold_of_a_runs_layers_is_the_vectors_effective_policy(case: dict[str, Any]) -> None:
    assert _fold(_layers(case)) == case["effective"]


@pytest.mark.parametrize("case", APPROVAL_VECTORS["documents"], ids=lambda case: case["id"])
def test_cpd3_40_the_resolver_folds_the_approvals_dimension_the_same_way(case: dict[str, Any]) -> None:
    layers = _layers(case)
    resolved = DimensionPolicyResolver().resolve(
        [ScopedLayer(LAYER_KINDS[min(i, len(LAYER_KINDS) - 1)], {"approvals": layer}) for i, layer in enumerate(layers)]
    )
    assert resolved.config is not None
    dimensions = resolved.config["dimensions"]
    assert isinstance(dimensions, dict)
    if layers:
        assert dimensions["approvals"]["value"] == case["effective"]  # pyright: ignore[reportIndexIssue, reportCallIssue, reportArgumentType]
    else:
        assert "approvals" not in dimensions


def _effect(kind: EffectKind, tool_id: str) -> ToolEffect:
    return ToolEffect(tool_id, kind, kind in ("read", "workspace"), SinkDescriptor(egress="external", args_leave_boundary=True))


@pytest.mark.parametrize("case", APPROVAL_VECTORS["documents"], ids=lambda case: case["id"])
def test_cpd3_41_the_order_decides_each_call_as_the_layered_policy(case: dict[str, Any]) -> None:
    policy = approval_policy(case["document"], org_id=case["orgId"], agent_key=case["agentKey"], log=LOG)
    ceiling = _fold(_layers(case))
    assert isinstance(ceiling, dict)
    classes: dict[str, ApprovalDecision] = ceiling["classes"]  # pyright: ignore[reportUnknownVariableType]
    tools: dict[str, ApprovalDecision] = ceiling["tools"]  # pyright: ignore[reportUnknownVariableType]
    call = ToolCall(id="c", name="t", arguments={})
    for kind in CLASSES:
        for tool in (*tools, UNRULED):
            assert policy(call, _effect(kind, tool)) == stricter(classes[kind], tools.get(tool, "allow")), (kind, tool)

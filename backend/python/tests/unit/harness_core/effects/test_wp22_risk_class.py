"""WP-22 OWD-7: a tool's risk class (read, write_internal, write_external, destructive, financial) in its metadata.

RK-1 effect and sink give read / write_internal / write_external.
RK-2 a source's mark makes a tool destructive or financial: a toolset's ``type`` tag (#4313's sends and clears, a
     delete), ``category: payments``, or the ``risk`` tag an MCP tool's kind sets; financial outranks destructive.
RK-3 the mark survives a per-call reclassification, and the generated contract lists the same classes.
RK-4 an MCP tool whose kind is destructive (a deleting word or ``destructiveHint``) carries the risk tag; others not.
RK-5 the risk class changes no approval: main #4313's rules still decide (toolset writes run, deletes ask).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.contracts.generated.enums import RISK_CLASSES as CONTRACT_RISK_CLASSES
from harness_core.effects.risk import RISK_CLASSES, riskier
from harness_core.kernel.durable.effects import classify_tool_effect, with_call_effect
from harness_core.tools.base import Tag
from harness_core.tools.tags import RISK_TAG_KEY, TAG_RISK_DESTRUCTIVE

if TYPE_CHECKING:
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter


@pytest.mark.parametrize(
    ("tags", "risk"),
    [
        ([Tag("effect", "read")], "read"),
        ([Tag("effect", "workspace")], "write_internal"),
        ([Tag("effect", "write"), Tag("sink", "self")], "write_internal"),
        ([Tag("effect", "write"), Tag("sink", "internal_audience")], "write_internal"),
        ([Tag("effect", "write")], "write_external"),
        ([Tag("effect", "execute"), Tag("sink", "none")], "write_internal"),
    ],
)
def test_rk1_effect_and_sink_give_the_class(tags: list[Tag], risk: str) -> None:
    assert classify_tool_effect("t", tags).risk == risk


@pytest.mark.parametrize(
    ("tags", "risk"),
    [
        ([Tag("effect", "write"), Tag("type", "destructive")], "destructive"),
        ([Tag("effect", "write"), Tag("type", "delete"), Tag("sink", "self")], "destructive"),
        ([Tag("effect", "write"), TAG_RISK_DESTRUCTIVE], "destructive"),
        ([Tag("effect", "write"), Tag("category", "payments")], "financial"),
        ([Tag("effect", "write"), Tag(RISK_TAG_KEY, "financial"), Tag("type", "destructive")], "financial"),
    ],
)
def test_rk2_a_sources_mark_makes_a_tool_destructive_or_financial(tags: list[Tag], risk: str) -> None:
    assert classify_tool_effect("t", tags).risk == risk


def test_rk3_the_mark_survives_a_per_call_class_and_the_contract_lists_the_classes() -> None:
    effect = classify_tool_effect("execute_query", [Tag("effect", "write"), Tag("type", "destructive")])
    assert with_call_effect(effect, "read").risk == "destructive"
    assert RISK_CLASSES == CONTRACT_RISK_CLASSES
    assert riskier("financial", "destructive") == "financial" and riskier("read", "write_internal") == "write_internal"


@pytest.mark.parametrize(("kind", "marked"), [("destructive", True), ("write", False), ("read", False)])
def test_rk4_an_mcp_tools_kind_sets_its_risk_tag(kind: str, marked: bool) -> None:
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter

    adapter = object.__new__(MCPToolAdapter)
    adapter._tool_info = SimpleNamespace(kind=kind, annotations=None, name="t")  # pyright: ignore[reportPrivateUsage]
    adapter._server = SimpleNamespace(instance={})  # pyright: ignore[reportPrivateUsage]
    tags = cast("MCPToolAdapter", adapter).tags
    assert (TAG_RISK_DESTRUCTIVE in tags) is marked


def test_rk5_the_risk_class_changes_no_starting_approval_rule() -> None:
    from app.agents.agent_loop.tool_approvals import starting_rule

    assert starting_rule(kind="write", source="toolset") == "allow"
    assert starting_rule(kind="destructive", source="toolset") == "ask"
    assert starting_rule(kind="write", source="mcp") == "ask"

"""Here no rule layer governs the platform's web tools, and every other layer's ask is answered once, as each card asks
it; an edition's ``LayerAskAnswers``, read at call time, decides instead and is handed the run's grants and the
product's destinations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app import edition_config
from app.harness.approvals import PlatformWebRuns, approval_policy, layer_ask_answers
from app.harness.flow_destination import pipeshub_destination
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from harness_core.tools.tags import TAG_EFFECT_READ
from tests.support.web_tools import product_web_tools

if TYPE_CHECKING:
    import pytest

LOG = HarnessLog.named("tests.layer_ask_answers")


def test_without_an_edition_s_answers_no_layer_governs_web_and_every_other_ask_stands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edition_config, "layer_ask_answers", None)
    governance = approval_policy({"default": {"classes": {"read": "ask"}}}, org_id="o", agent_key=None, log=LOG)
    policy = layer_ask_answers(governance, context=SimpleNamespace(), grants=None, log=LOG)  # type: ignore[arg-type]
    assert isinstance(policy, PlatformWebRuns) and policy.base is governance
    for name in ("dynamic__web_search", "dynamic__fetch_url"):
        tool = product_web_tools()[name]
        effect = classify_tool_effect(tool.name, tool.tags)
        assert policy(ToolCall(id="c", name=name, arguments={}), effect) == "allow"
        assert policy.ask_reason(ToolCall(id="c", name=name, arguments={}), effect) is None
    read = classify_tool_effect("jira_get_issue", (TAG_EFFECT_READ,))
    call = ToolCall(id="r", name="jira_get_issue", arguments={})
    assert policy(call, read) == "ask"
    assert policy.ask_reason(call, read) == governance.ask_reason(call, read)


def test_an_edition_s_answers_wrap_the_run_s_governance(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Answers:
        def answering(self, governance: object, **kwargs: object) -> object:
            seen.update(kwargs, governance=governance)
            return "wrapped"

    monkeypatch.setattr(edition_config, "layer_ask_answers", Answers())
    governance = approval_policy({}, org_id="o", agent_key=None, log=LOG)
    context, grants = SimpleNamespace(), object()
    assert layer_ask_answers(governance, context=context, grants=grants, log=LOG) == "wrapped"  # type: ignore[arg-type]
    assert seen["governance"] is governance and seen["context"] is context and seen["grants"] is grants
    assert seen["destination"] is pipeshub_destination

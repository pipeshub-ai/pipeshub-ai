"""The approvals document's rule layers: the operator's ``default`` and ``agents``, and nothing else read silently.

A top-level map the schema does not hold refuses the document as a whole, so a run asks before writes rather than run
past a rule it cannot see. An edition's rule layers (``edition_config.org_approval_rules``) join in order: default,
the edition's org layers, ``agents[agentKey]``, the edition's agent layers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app import edition_config
from app.harness.approvals import approval_policy, org_approval_rules
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import ToolEffect, tool_id_for
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.effects.policy import ApprovalRules, RuleLayer

LOG = HarnessLog.named("tests.approvals_layers")
CALL = ToolCall(id="c", name="t", arguments={})
WRITE = ToolEffect(tool_id_for("t"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
READ = ToolEffect(tool_id_for("t"), "read", True, SinkDescriptor(egress="none", args_leave_boundary=False))


def test_without_an_edition_s_layers_the_deployment_s_alone_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_config, "org_approval_rules", None, raising=False)
    assert org_approval_rules() is None
    policy = approval_policy({"default": {"writes": "ask"}, "agents": {"a1": {"writes": "deny"}}}, org_id="o", agent_key="a1", log=LOG)
    assert policy.scopes == ("deployment", "agent")
    assert policy(CALL, WRITE) == "deny"


@pytest.mark.parametrize("document", [{"reviewerLists": {"o": ["u-1"]}}, {"default": {}, "surprise": {}}])
def test_a_map_the_schema_does_not_hold_refuses_the_document_and_writes_ask(document: JsonValue) -> None:
    policy = approval_policy(document, org_id="o", agent_key="a1", log=LOG)
    assert (policy(CALL, WRITE), policy(CALL, READ)) == ("ask", "allow")


class _Edition:
    """An edition's layers kept under one top-level map of its own: ``{"extra": {orgId: {"org": ..., "agents": ...}}}``."""

    def org_layers(
        self, doc: Mapping[str, JsonValue], org_id: str, read: Callable[[JsonValue, str], ApprovalRules]
    ) -> Sequence[tuple[ApprovalRules, RuleLayer]]:
        entry = doc.get("extra", {}).get(org_id, {})  # type: ignore[union-attr]
        return [(read(entry["org"], "extra"), "org")] if "org" in entry else []  # type: ignore[operator, index]

    def agent_layers(
        self, doc: Mapping[str, JsonValue], org_id: str, agent_key: str, read: Callable[[JsonValue, str], ApprovalRules]
    ) -> Sequence[tuple[ApprovalRules, RuleLayer]]:
        rule = doc.get("extra", {}).get(org_id, {}).get("agents", {}).get(agent_key)  # type: ignore[union-attr]
        return [] if rule is None else [(read(rule, "extra"), "agent")]


def test_an_edition_s_layers_join_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_config, "org_approval_rules", _Edition(), raising=False)
    monkeypatch.setattr("app.harness.approvals._SECTIONS", frozenset({"default", "agents", "extra"}))
    monkeypatch.setattr("app.harness.approvals._MAPS", frozenset({"agents", "extra"}))
    document = {
        "default": {"classes": {"read": "ask"}},
        "agents": {"a1": {"writes": "ask"}},
        "extra": {"o": {"org": {"writes": "allow"}, "agents": {"a1": {"writes": "deny"}}}},
    }
    policy = approval_policy(document, org_id="o", agent_key="a1", log=LOG)  # type: ignore[arg-type]
    assert policy.scopes == ("deployment", "org", "agent", "agent")
    assert [dict(layer.classes) for layer in policy.layers] == [
        {"read": "ask"},
        {"write": "allow", "execute": "allow"},
        {"write": "ask", "execute": "ask"},
        {"write": "deny", "execute": "deny"},
    ]
    assert policy(CALL, WRITE) == "deny"
    elsewhere = approval_policy(document, org_id="other", agent_key=None, log=LOG)  # type: ignore[arg-type]
    assert elsewhere.scopes == ("deployment",)
    unreadable = approval_policy({"extra": {"o": {"org": {"writes": "sometimes"}}}}, org_id="o", agent_key=None, log=LOG)  # type: ignore[arg-type]
    assert unreadable(CALL, WRITE) == "ask"

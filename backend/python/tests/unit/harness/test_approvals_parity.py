"""AD-1..AD-3: Python reads /services/agentHarness/approvals by the schema Node validates it with.

The vector table is shared with Node (``approvals-parity.test.ts``); both languages must accept exactly
the rules ``approval-rules.schema.json`` accepts and read every document to the same effective policy.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.approvals import approval_policy
from harness_core.effects.flow import layer_rule, strictest_flows
from harness_core.effects.policy import ApprovalConfigInvalid, parse_rules, stricter
from harness_core.logs import HarnessLog
from tests.unit.contracts_harness.support import (
    CONTRACTS,
    ContractSchema,
    instance_schemas,
    validator,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.policy import LayeredApprovalPolicy
    from harness_core.effects.types import ApprovalDecision

VECTORS: dict[str, Any] = json.loads((CONTRACTS / "vectors" / "approvals.json").read_text(encoding="utf-8"))
LOG = HarnessLog.named("tests.approvals_parity")
CLASSES = ("read", "workspace", "write", "execute")


def _schema(rel: str) -> ContractSchema:
    return next(schema for schema in instance_schemas() if schema.rel == rel)


def _valid(rel: str, instance: JsonValue) -> bool:
    return bool(validator(_schema(rel)).is_valid(instance))  # pyright: ignore[reportUnknownMemberType]


@pytest.mark.parametrize("case", VECTORS["rules"], ids=lambda case: case["id"])
def test_ad1_ad2_python_accepts_exactly_the_rules_the_schema_accepts(case: dict[str, Any]) -> None:
    assert _valid("harness/core/approval-rules.schema.json", case["rule"]) is case["valid"]
    if not case["valid"]:
        with pytest.raises(ApprovalConfigInvalid):
            _ = parse_rules(case["rule"])
        return
    rules = parse_rules(case["rule"])
    normalized: dict[str, Any] = {"classes": dict(rules.classes), "tools": dict(rules.tools)}
    if rules.flow is not None:
        normalized["flow"] = {"level": rules.flow.level, "above": rules.flow.above}
    assert normalized == case["normalized"]


def _effective(policy: LayeredApprovalPolicy) -> dict[str, Any]:
    classes: dict[str, ApprovalDecision] = dict.fromkeys(CLASSES, "allow")
    tools: dict[str, ApprovalDecision] = {}
    for layer in policy.layers:
        for kind, level in layer.classes.items():
            classes[kind] = stricter(classes[kind], level)
        for tool, level in layer.tools.items():
            tools[tool] = stricter(tools.get(tool, "allow"), level)
    effective: dict[str, Any] = {"classes": classes, "tools": tools}
    flows = strictest_flows(layer_rule(flow) for flow in policy.flows)
    if flows:
        effective["flow"] = [{"level": rule.level, "above": rule.above} for rule in flows]
    return effective


@pytest.mark.parametrize("case", VECTORS["documents"], ids=lambda case: case["id"])
def test_ad3_python_reads_every_document_as_node_does(case: dict[str, Any]) -> None:
    document: JsonValue = case["document"]
    if document is not None:
        assert _valid("harness/ext/pipeshub/approvals-document.schema.json", document) is case["schemaValid"]
    policy = approval_policy(document, org_id=case["orgId"], agent_key=case["agentKey"], log=LOG)
    assert _effective(policy) == case["effective"]


def test_ad3_an_unknown_top_level_key_fails_closed_once(caplog: pytest.LogCaptureFixture) -> None:
    policy = approval_policy({"org": {"org-1": {"writes": "allow"}}}, org_id="org-1", agent_key=None, log=LOG)
    assert _effective(policy)["classes"]["write"] == "ask"
    assert [r.getMessage() for r in caplog.records if "approvals.config_invalid" in r.getMessage()] != []
    assert sum("approval_document_unknown_key" in str(r.__dict__) for r in caplog.records) == 1

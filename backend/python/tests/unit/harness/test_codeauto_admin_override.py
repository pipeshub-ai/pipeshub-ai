"""CODEAUTO CA-13..CA-15 on the product's own tools and approvals document: the human stack's rule
(`writes: ask`, deployment/e2e/seed_config.py) no longer asks before `run_code` without network; a tool rule for it
asks again; `run_code` with network and `install_packages` still ask."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.agents.agent_loop.sandbox_bridge import register_coding_sandbox_tools
from app.harness.approvals import UNREADABLE_LAYER, approval_policy
from harness_core.effects.policy import LayeredApprovalPolicy
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from harness_core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from harness_core.effects.types import ToolEffect

ORG = "org-1"
RUN_CODE_ID = "pipeshub@sandbox/run_code"
INSTALL_ID = "pipeshub@sandbox/install_packages"
LOG = HarnessLog.named("tests.codeauto")


def _sandbox_effects(*, network: bool) -> dict[str, ToolEffect]:
    """Each sandbox tool's effect from its real tags, under the id ``SandboxSource`` gives it."""
    registry = ToolRegistry()
    register_coding_sandbox_tools(registry, MagicMock(), allow_network=network)
    return {
        name: dataclasses.replace(classify_tool_effect(name, registry.tags_for_name(name)), tool_id=f"pipeshub@sandbox/{name}")
        for name in ("run_code", "install_packages", "read_sandbox_file")
    }


def _org(rules: dict[str, object]) -> LayeredApprovalPolicy:
    return approval_policy({"default": rules}, org_id=ORG, agent_key=None, log=LOG)  # type: ignore[dict-item]


def _decide(policy: LayeredApprovalPolicy, effect: ToolEffect) -> str:
    return policy(ToolCall(id="c", name=effect.tool_id.rsplit("/", 1)[-1], arguments={}), effect)


# CA-13
def test_ca13_the_seeded_writes_switch_no_longer_asks_before_offline_run_code() -> None:
    effects = _sandbox_effects(network=False)
    assert (effects["run_code"].effect, effects["run_code"].sink.egress) == ("execute", "none")
    org = _org({"writes": "ask"})
    assert _decide(org, effects["run_code"]) == "allow"
    assert _decide(org, effects["read_sandbox_file"]) == "allow"
    assert _decide(LayeredApprovalPolicy((UNREADABLE_LAYER,)), effects["run_code"]) == "allow"


# CA-14
def test_ca14_run_code_with_network_and_install_packages_still_ask() -> None:
    org = _org({"writes": "ask"})
    networked = _sandbox_effects(network=True)
    assert networked["run_code"].sink.egress == "external"
    assert _decide(org, networked["run_code"]) == "ask"
    for network in (False, True):
        install = _sandbox_effects(network=network)["install_packages"]
        assert install.tool_id == INSTALL_ID and install.sink.egress == "external"
        assert _decide(org, install) == "ask"


# CA-15
@pytest.mark.parametrize(("level", "decision"), [("ask", "ask"), ("deny", "deny")])
def test_ca15_a_rule_for_run_code_overrides_the_default(level: str, decision: str) -> None:
    run_code = _sandbox_effects(network=False)["run_code"]
    org = _org({"writes": "ask", "tools": {RUN_CODE_ID: level}})
    assert _decide(org, run_code) == decision
    if decision == "ask":
        reason = org.ask_reason(ToolCall(id="c", name="run_code", arguments={}), run_code)
        assert reason == {"rule": "tool", "layer": "deployment", "toolId": RUN_CODE_ID, "conversationGrant": False}


def test_ca15_an_agents_own_rule_overrides_it_too() -> None:
    run_code = _sandbox_effects(network=False)["run_code"]
    document = {"default": {"writes": "ask"}, "agents": {"agent-1": {"tools": {RUN_CODE_ID: "ask"}}}}
    policy = approval_policy(document, org_id=ORG, agent_key="agent-1", log=LOG)  # type: ignore[arg-type]
    assert _decide(policy, run_code) == "ask"
    assert _decide(approval_policy(document, org_id=ORG, agent_key="agent-2", log=LOG), run_code) == "allow"  # type: ignore[arg-type]

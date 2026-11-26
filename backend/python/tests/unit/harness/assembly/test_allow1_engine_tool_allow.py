"""MCPNOTE-FU-2 / ALLOW-1: a person's (or an admin's) explicit Allow of one tool runs on an engine job unasked.

The lane (``test_mcpnote_unlisted_servers.py``) showed it: on the default chat and a saved agent the person's Allow of
an MCP write runs it, on an engine job it still asked. The engine chain is ``engine_guarded``: main's per-tool rules
(``ToolRulesPolicy``) over the org's governance, under the flow guard, under the engine's release default
(``ENGINE_APPROVALS``: a write that leaves the person's space asks) through ``DefaultAskPolicy``, which took the
stricter. The org's ``writes: allow`` was not it. ADR-OWD-09 D-ALLOW-1: a specific per-tool Allow lifts that class
default for the one tool; Block, the flow guard and an org rule that asks still win.

Each case builds the run's real rules (the assembler's ``ToolRulesPolicy``, refreshed from the stored rules) and the
engine chain the engine assembler builds around them.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app import edition_config
from app.agents.constants.mcp_server_constants import get_mcp_user_tool_rules_path
from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
from app.harness.approvals import approval_policy, engine_guarded
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.labels import SessionSourceLedger
from app.harness.tool_rules import ToolRulesPolicy
from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    FLOW_RULE,
    FlowGuardedPolicy,
    FlowRule,
    GrantingLedger,
)
from harness_core.effects.types import ExplainsAsk, ToolEffect, refresh_policy
from harness_core.engines.policy import engine_default_approvals
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.effects.types import ApprovalPolicy

TOOL = "mcp_github_list_prs"
LOG = HarnessLog.named("tests.allow1")


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment(), patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}):
        yield


class _ReadPrivate(SessionSourceLedger):
    """A session that has read a private record: the flow guard's ``ask`` holds what leaves the org."""

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return frozenset({"rec-private"})


async def _engine_chain(
    rule: str | None, org_writes: str, *, flow: str | None = None, read_private: bool = False
) -> tuple[ApprovalPolicy, ToolEffect]:
    services = w.Services()
    if rule is not None:
        services.config.put(get_mcp_user_tool_rules_path(w.ACTOR, w.MCP), {"tools": {"list_prs": rule}})
    return await _chain(services, f"mcp@{w.MCP}/list_prs", TOOL, org_writes, flow=flow, read_private=read_private)


async def _chain(
    services: w.Services, tool_id: str, name: str, org_writes: str, *, flow: str | None = None,
    read_private: bool = False,
) -> tuple[ApprovalPolicy, ToolEffect]:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    runtime = await w.first_use(await assembler.build(w.claim(w.pinned_spec(tool_ids=[tool_id]))))
    await refresh_policy(runtime.approval)
    rules = runtime.approval
    while not isinstance(rules, ToolRulesPolicy):
        assert isinstance(rules, FlowGuardedPolicy), type(rules)
        rules = rules.base
    governance = approval_policy({"default": {"writes": org_writes}}, org_id=w.ORG, agent_key=None, log=LOG)
    effect = runtime.tools.describe(name)
    assert effect is not None
    ledger = GrantingLedger(_ReadPrivate() if read_private else SessionSourceLedger())
    chain = engine_guarded(
        engine_default_approvals(),
        dataclasses.replace(rules, base=governance),
        rules=(FlowRule(flow),) if flow else (),  # type: ignore[arg-type]
        ledger=ledger,
        run_id="run-1",
        log=LOG,
    )
    await refresh_policy(chain)
    return chain, effect


def _rule_of(chain: ApprovalPolicy, effect: ToolEffect, name: str = TOOL) -> object:
    assert isinstance(chain, ExplainsAsk)
    return (chain.ask_reason(ToolCall(id="c", name=name, arguments={}), effect) or {}).get("rule")


# (the tool's own rule, the org's writes rule, the flow rule, the session read private data, decided, the rule asked)
CASES = [
    # D-ALLOW-1: a specific Allow lifts the engine's class default for that tool.
    ("allow", "allow", None, False, "allow", None),
    # An org rule that asks wins over a person's Allow.
    ("allow", "ask", None, False, "ask", "writes"),
    ("allow", "deny", None, False, "deny", None),
    # The flow guard wins over a person's Allow.
    ("allow", "allow", "ask", True, "ask", FLOW_RULE),
    ("allow", "allow", "deny", True, "deny", None),
    # The person's own Ask and Block.
    ("ask", "allow", None, False, "ask", "tool"),
    ("block", "allow", None, False, "deny", None),
    # No rule: main's default asks, and the engine's release default stays for an unconfigured tool.
    (None, "allow", None, False, "ask", "writes"),
    (None, "ask", None, False, "ask", "writes"),
]


@pytest.mark.parametrize(("rule", "org", "flow", "read_private", "decided", "asked_by"), CASES)
async def test_allow1_the_engine_chain_decides_by_the_precedence_table(
    rule: str | None, org: str, flow: str | None, read_private: bool, decided: str, asked_by: str | None
) -> None:
    chain, effect = await _engine_chain(rule, org, flow=flow, read_private=read_private)
    assert chain(ToolCall(id="c", name=TOOL, arguments={}), effect) == decided
    if decided == "ask":
        assert _rule_of(chain, effect) == asked_by
    elif decided == "allow":
        assert _rule_of(chain, effect) is None


async def test_allow1_the_engine_default_still_asks_where_only_main_s_default_allows() -> None:
    """An Allow that is no one's setting (main's default for a tool with hints, or no rules at all) lifts nothing:
    with the rules off, the tool has no rule and the engine's release default asks, as before."""
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "false"}):
        chain, effect = await _engine_chain(None, "allow")
    assert chain(ToolCall(id="c", name=TOOL, arguments={}), effect) == "ask"
    assert _rule_of(chain, effect) == ENGINE_WRITES_RULE


@pytest.mark.parametrize(("admin", "decided"), [("allow", "allow"), (None, "ask")])
async def test_allow1_an_admin_s_allow_of_a_toolset_tool_lifts_it_for_the_org(admin: str | None, decided: str) -> None:
    """An admin's Allow of one toolset tool, for the organization (the edition's org toolset rules), is that tool's
    specific rule too. Without it a toolset write starts at main's Allow (#4313), which is no one's setting: the
    engine's release default still asks before it leaves the person's space."""
    services = w.Services()
    if admin is not None:
        services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": admin}})
    with patch.object(edition_config, "organization_toolset_tool_rules", True):
        chain, effect = await _chain(services, f"jira@{w.JIRA}/create_issue", "jira__create_issue", "allow")
    assert effect.sink.egress not in ("none", "self"), effect
    assert chain(ToolCall(id="c", name="jira__create_issue", arguments={}), effect) == decided
    if decided == "ask":
        assert _rule_of(chain, effect, "jira__create_issue") == ENGINE_WRITES_RULE

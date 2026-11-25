"""SYNC3: main's per-tool approval rules (#4003, #4256) on a durable run.

Today's loop asks with its card; a durable run has no card, so main's gate alone would block every
Ask tool as unattended. The kernel asks instead (``ToolRulesPolicy``), and the gate refuses only what
is blocked.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app import edition_config
from app.agents.constants.mcp_server_constants import get_mcp_user_tool_rules_path
from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.tool_rules import ToolRulesPolicy
from harness_core.effects.flow import AskDestination, FlowGuardedPolicy
from harness_core.effects.types import ExplainsAsk, ExplainsDenial, refresh_policy
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver import AssembledRuntime

JIRA_SEARCH = f"jira@{w.JIRA}/search_issues"
JIRA_CREATE = f"jira@{w.JIRA}/create_issue"
_CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


@pytest.fixture
def org_rules() -> Iterator[None]:
    """Admins set toolset tools' rules for the organization: main's edition extension point (#4326). Off (the
    community default), a toolset tool starts from what it does to data and no admin rule is read."""
    with patch.object(edition_config, "organization_toolset_tool_rules", True):
        yield


async def _build(services: w.Services) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    runtime = await w.first_use(await assembler.build(w.claim(w.pinned_spec(tool_ids=[JIRA_SEARCH, JIRA_CREATE]))))
    await refresh_policy(runtime.approval)
    return runtime


def _decide(runtime: AssembledRuntime, name: str) -> str:
    call = ToolCall(id="c", name=name, arguments={})
    effect = runtime.tools.describe(name)
    assert effect is not None
    return runtime.approval(call, effect)


async def test_a_toolset_write_runs_without_asking_by_default() -> None:
    """Main #4313: a toolset tool that changes data starts at Allow; only a deleting one asks."""
    runtime = await _build(w.Services())
    assert _decide(runtime, "jira__create_issue") == "allow"
    assert _decide(runtime, "jira__search_issues") == "allow"


@pytest.mark.usefixtures("org_rules")
async def test_an_ask_tool_asks_through_the_kernel_and_runs_once_approved() -> None:
    services = w.Services()
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": "ask"}})
    runtime = await _build(services)
    assert _decide(runtime, "jira__create_issue") == "ask"
    assert _decide(runtime, "jira__search_issues") == "allow"
    # The kernel asked; the call it then executes is not blocked as unattended by main's gate.
    ran = await runtime.tools.execute(ToolCall(id="c", name="jira__create_issue", arguments={"title": "x"}), _CTX)
    assert ran.status == "ok", ran.content
    assert "J-1" in str(ran.content)


@pytest.mark.usefixtures("org_rules")
async def test_an_admin_block_refuses_with_mains_wording() -> None:
    services = w.Services()
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": "block"}})
    runtime = await _build(services)
    assert _decide(runtime, "jira__create_issue") == "deny"
    assert isinstance(runtime.approval, ExplainsDenial)
    effect = runtime.tools.describe("jira__create_issue")
    rule, text = runtime.approval.denial(ToolCall(id="c", name="jira__create_issue"), effect)  # type: ignore[arg-type]
    assert rule == "tool_rules"
    assert "blocked in the organization's tool approval rules" in text
    refused = await runtime.tools.execute(ToolCall(id="c", name="jira__create_issue", arguments={"title": "x"}), _CTX)
    assert refused.status == "error"


async def test_an_mcp_tool_without_hints_asks_through_the_kernel() -> None:
    # The world switches main's MCP rules off for the runs it tests; here they are on, as shipped (#4003). Not
    # monkeypatch: set up before the world's env patch, its undo restored the world's "false" for later tests.
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}):
        await _mcp_tool_asks()


async def _mcp_tool_asks() -> None:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    runtime = await w.first_use(await assembler.build(w.claim(w.pinned_spec(tool_ids=[f"mcp@{w.MCP}/list_prs"]))))
    await refresh_policy(runtime.approval)
    # No hints: main's starting rule is Ask; the durable run asks rather than blocking it as unattended.
    assert _decide(runtime, "mcp_github_list_prs") == "ask"


def _reason(runtime: AssembledRuntime, name: str) -> object:
    """The rule the card names (the run's flow context rides beside it: `sensitivity`, `sources`)."""
    assert isinstance(runtime.approval, ExplainsAsk)
    effect = runtime.tools.describe(name)
    assert effect is not None
    reason = runtime.approval.ask_reason(ToolCall(id="c", name=name, arguments={}), effect) or {}
    return {key: reason.get(key) for key in ("rule", "layer", "toolId", "conversationGrant")}


@pytest.mark.usefixtures("org_rules")
async def test_sync4_the_card_says_why_mains_rule_asks() -> None:
    """SYNC3 open item: a durable card for main's rule ask carries its reason, as the run's own asks do."""
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}):
        runtime = await _mcp_runtime(w.Services())
        assert _reason(runtime, "mcp_github_list_prs") == {
            "rule": "writes", "layer": "deployment", "toolId": MCP_LIST, "conversationGrant": False,
        }
    services = w.Services()
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"search_issues": "ask"}})
    runtime = await _build(services)
    assert _reason(runtime, "jira__search_issues") == {
        "rule": "tool", "layer": "org", "toolId": JIRA_SEARCH, "conversationGrant": False,
    }


@pytest.mark.usefixtures("org_rules")
async def test_an_admin_allow_runs_without_asking() -> None:
    services = w.Services()
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": "allow"}})
    runtime = await _build(services)
    assert _decide(runtime, "jira__create_issue") == "allow"


class _Granted:
    """A conversation's "allow for this conversation" answers, per tool and destination key."""

    def __init__(self, *pairs: tuple[str, str]) -> None:
        self.pairs = set(pairs)
        # C4.10 ``interactions.conversationGrants``: the run lets a person answer for the conversation.
        self.enabled = True

    def allows(self, tool_id: str, key: str) -> bool:
        return (tool_id, key) in self.pairs


_PROJ = AskDestination("PROJ", "key-proj")


def _granting(runtime: AssembledRuntime, grants: _Granted) -> FlowGuardedPolicy:
    """The run's own approval stack (flow guard over main's rules) with ``grants`` as the session's answers."""
    flow = runtime.approval
    assert isinstance(flow, FlowGuardedPolicy) and isinstance(flow.base, ToolRulesPolicy)
    where = {"grants": grants, "destination": lambda call, effect: _PROJ}
    return dataclasses.replace(flow, base=dataclasses.replace(flow.base, **where), **where)  # type: ignore[arg-type]


def _ask(policy: FlowGuardedPolicy, runtime: AssembledRuntime, name: str) -> tuple[str, object]:
    effect = runtime.tools.describe(name)
    assert effect is not None
    call = ToolCall(id="c", name=name, arguments={})
    return policy(call, effect), (policy.ask_reason(call, effect) or {}).get("conversationGrant")


async def test_sync4_a_conversation_grant_answers_mains_default_write_rule() -> None:
    """PW-094: main's default Ask before a write is offered for the conversation and the grant
    answers it, for that tool and destination only; a conversation without the grant is asked again. Since main
    #4313 a toolset write starts at Allow, so the default Ask here is an MCP tool's."""
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}):
        runtime = await _mcp_runtime(w.Services())
        tool = "mcp_github_list_prs"
        assert _ask(_granting(runtime, _Granted()), runtime, tool) == ("ask", True)
        assert _ask(_granting(runtime, _Granted((MCP_LIST, _PROJ.key))), runtime, tool)[0] == "allow"
        elsewhere = _Granted((MCP_LIST, "key-elsewhere"))
        assert _ask(_granting(runtime, elsewhere), runtime, tool)[0] == "ask"


@pytest.mark.usefixtures("org_rules")
async def test_sync4_an_admins_explicit_ask_is_never_granted() -> None:
    services = w.Services()
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": "ask"}})
    runtime = await _build(services)
    granted = _Granted((JIRA_CREATE, _PROJ.key))
    assert _ask(_granting(runtime, granted), runtime, "jira__create_issue") == ("ask", False)


@pytest.mark.usefixtures("org_rules")
async def test_sync4_an_admins_block_still_blocks_under_a_grant() -> None:
    services = w.Services()
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": "block"}})
    runtime = await _build(services)
    granted = _Granted((JIRA_CREATE, _PROJ.key))
    assert _ask(_granting(runtime, granted), runtime, "jira__create_issue")[0] == "deny"


# BUGS-B: "Don't ask again" (main's remember) on the durable card for main's rule asks.

MCP_LIST = f"mcp@{w.MCP}/list_prs"


async def _mcp_runtime(services: w.Services) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    runtime = await w.first_use(await assembler.build(w.claim(w.pinned_spec(tool_ids=[MCP_LIST]))))
    await refresh_policy(runtime.approval)
    return runtime


def _rule_grant(runtime: AssembledRuntime, name: str) -> object:
    assert isinstance(runtime.approval, ExplainsAsk)
    effect = runtime.tools.describe(name)
    assert effect is not None
    return (runtime.approval.ask_reason(ToolCall(id="c", name=name, arguments={}), effect) or {}).get("ruleGrant")


async def test_bugsb_dont_ask_again_is_offered_where_mains_default_rule_asks_for_the_persons_own_tool() -> None:
    """Main offers "Don't ask again" for an MCP tool (the person's own rules); per PW-094 the durable card offers it
    where main's default asked, never for an explicit rule, and never for a toolset (the admin's rules)."""
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}):
        assert _rule_grant(await _mcp_runtime(w.Services()), "mcp_github_list_prs") == {
            "instanceId": w.MCP, "toolName": "list_prs",
        }
        services = w.Services()
        services.config.put(get_mcp_user_tool_rules_path(w.ACTOR, w.MCP), {"tools": {"list_prs": "ask"}})
        assert _rule_grant(await _mcp_runtime(services), "mcp_github_list_prs") is None
    assert _rule_grant(await _build(w.Services()), "jira__create_issue") is None


@pytest.mark.parametrize(("saved", "decided"), [("allow", "allow"), ("block", "deny")])
async def test_bugsb_a_rule_saved_from_the_card_applies_to_the_runs_next_call(saved: str, decided: str) -> None:
    """The card's "Don't ask again" saves the person's rule (Allow, or Block for a Deny, as main's remember); the same
    run reads it at its next decision instead of asking again."""
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}):
        services = w.Services()
        runtime = await _mcp_runtime(services)
        assert _decide(runtime, "mcp_github_list_prs") == "ask"
        services.config.put(get_mcp_user_tool_rules_path(w.ACTOR, w.MCP), {"tools": {"list_prs": saved}})
        await refresh_policy(runtime.approval)
        assert _decide(runtime, "mcp_github_list_prs") == decided


def test_bugsb_an_older_journal_never_gets_the_rule_grant() -> None:
    from harness_core.effects.flow import reason_for_journal

    reason = {"rule": "writes", "layer": "deployment", "toolId": MCP_LIST, "conversationGrant": False,
              "ruleGrant": {"instanceId": w.MCP, "toolName": "list_prs"}}
    assert reason_for_journal(reason, layers=True, rule_grants=True) == reason
    assert "ruleGrant" not in (reason_for_journal(reason, layers=True) or {})
    assert "ruleGrant" not in (reason_for_journal(reason, layers=False) or {})


class _ApprovalsRequired:
    async def requires_admin_approval(self, org_id: str, config_service: object) -> bool:
        return True


@pytest.mark.parametrize("admission", ["oss", "approvals_required"])
@pytest.mark.parametrize(("rule", "decided"), [("ask", "ask"), ("allow", "allow"), ("block", "deny"), (None, "ask")])
async def test_d_mcp_5_an_mcp_tools_own_rule_decides_each_call_whatever_admits_it(
    admission: str, rule: str | None, decided: str
) -> None:
    """MCPNOTE-1, D-MCP-5: dropping the admin's pre-approval (which tools a run may use) leaves each call's rule (asks
    before the call) as it was: the person's Ask asks, Allow runs, Block refuses; a tool with no rule yet starts
    from what it does (no hints: Ask). The rule lives in its own record, never in the hub's manifest."""
    services = w.Services()
    if rule is not None:
        services.config.put(get_mcp_user_tool_rules_path(w.ACTOR, w.MCP), {"tools": {"list_prs": rule}})
    policy = _ApprovalsRequired() if admission == "approvals_required" else None
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}), \
         patch.object(edition_config, "mcp_tool_admission", policy):
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
        assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
        runtime = await w.first_use(await assembler.build(w.claim(w.pinned_spec(tool_ids=[f"mcp@{w.MCP}/list_prs"]))))
        await refresh_policy(runtime.approval)
        assert _decide(runtime, "mcp_github_list_prs") == decided

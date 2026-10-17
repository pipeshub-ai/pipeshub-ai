"""Approvals for toolset tools (Jira, Slack, ...): `tool_loader` records which instance each external
toolset tool runs on, and `tool_approvals` checks its calls under that instance's rules, set by an
admin for everyone. The card has no "Don't ask again", and an answer resumes the stopped call the
way it does for an MCP tool."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.events.base import EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.tools.base import Tag
from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.toolset import ToolsetBuilder
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_approvals import (
    PendingCall,
    ToolApprovalAnswer,
    ToolRules,
)
from app.agents.agent_loop.tool_loader import PipesHubToolLoader, _record_for_approvals
from app.agents.tools.factories.registry import ClientFactoryRegistry
from tests.unit.agents.agent_loop.test_tool_approvals import (
    _context,
    _Request,
    _result_of,
    _Store,
    run_request,
)


class _Jira:
    """A connector toolset as the loader builds one: a client, and `@tool` methods."""

    created: list[str] = []

    def __init__(self, client: Any = None) -> None:  # noqa: ANN401
        self.client = client

    @tool(path="/tools/jira/search_issues", short_description="Search issues", description="Search Jira issues.",
          tags=[Tag(key="type", value="read")])
    async def search_issues(self, jql: str) -> tuple[bool, str]:
        return True, "[]"

    @tool(path="/tools/jira/create_issue", short_description="Create an issue", description="Create a Jira issue.",
          tags=[Tag(key="type", value="write")])
    async def create_issue(self, title: str) -> tuple[bool, str]:
        _Jira.created.append(title)
        return True, f"created {title}"

    @tool(path="/tools/jira/delete_issue", short_description="Delete an issue", description="Delete a Jira issue.",
          tags=[Tag(key="type", value="destructive")])
    async def delete_issue(self, key: str) -> tuple[bool, str]:
        return True, f"deleted {key}"


@pytest.fixture(autouse=True)
def _nothing_created() -> None:
    _Jira.created = []


_INSTANCE = ("inst-j", "Jira (Engineering)")


def _tools(context: Any, instance: tuple[str, str] | None = _INSTANCE) -> dict[str, Any]:  # noqa: ANN401
    """The toolset's tools by name, recorded for the gate as the loader records them."""
    tools = {t.name: t for t in ToolsetBuilder(_Jira(), name="jira", description="Jira", path_prefix="/tools/jira").tools}
    for t in tools.values():
        _record_for_approvals(context, t, instance, fallback_owner="jira")
    return tools


async def _gate(context: Any, name: str, *, instance: tuple[str, str] | None = _INSTANCE, **arguments: Any) -> ToolCallContext:  # noqa: ANN401
    ctx = ToolCallContext(tool_path=_tools(context, instance)[name].path, tool_input=dict(arguments))
    reached: list[bool] = []

    async def _next() -> None:
        reached.append(True)

    await approvals.approval_gate(context)(ctx, _next)
    assert reached == [True], "the gate passes the call on; the executor acts on the decision"
    return ctx


async def _set_rules(store: _Store, **rules: str) -> None:
    """What an admin saves for the instance."""
    await approvals.save_instance_rules(store, ToolRules(tools=rules), instance_id="inst-j")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------

class _Factory:
    async def create_client(self, config_service: Any, logger: Any, config: dict, state: dict) -> dict:  # noqa: ANN401
        return {"config": config}


@pytest.fixture
def _jira_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ClientFactoryRegistry, "_factories", {"jira": _Factory()})
    monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)


async def _load(*, internal: bool = False, picked: list[str] | None = None) -> tuple[AgentContext, Any]:
    toolset: dict[str, Any] = {"name": "jira", "instanceId": "inst-j", "instanceName": "Jira (Engineering)"}
    if picked is not None:
        toolset["tools"] = [{"name": name} for name in picked]
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
        agent_toolsets=[toolset],
        toolset_configs={"inst-j": {"auth": {}}},
    )
    catalogue = MagicMock()
    catalogue.get_all_toolsets.return_value = {"jira": {"class": _Jira, "isInternal": internal, "description": "Jira"}}
    with patch("app.agents.registry.toolset_registry.get_toolset_registry", return_value=catalogue):
        registry = await PipesHubToolLoader().load(context)
    return context, registry


@pytest.mark.usefixtures("_jira_factory")
class TestTheLoader:
    async def test_records_each_tool_with_the_instance_its_credentials_come_from(self) -> None:
        context, registry = await _load()

        recorded = context.tool_state[approvals.TOOLSET_TOOLS_BY_PATH]
        assert set(recorded) == {"/tools/jira/search_issues", "/tools/jira/create_issue", "/tools/jira/delete_issue"}
        target = recorded["/tools/jira/create_issue"]
        assert (target.source, target.instance_id, target.tool_name, target.owner) == (
            "toolset", "inst-j", "create_issue", "Jira (Engineering)",
        )
        assert target.tool is registry.resolve_by_name("jira__create_issue")
        assert {path.rsplit("/", 1)[-1]: t.kind for path, t in recorded.items()} == {
            "search_issues": "read", "create_issue": "write", "delete_issue": "destructive",
        }

    async def test_only_the_tools_picked_for_the_agent_are_recorded(self) -> None:
        context, registry = await _load(picked=["create_issue"])

        assert list(context.tool_state[approvals.TOOLSET_TOOLS_BY_PATH]) == ["/tools/jira/create_issue"]
        assert not registry.has("jira__delete_issue")

    async def test_an_internal_toolsets_tools_arent_checked(self) -> None:
        context, registry = await _load(internal=True)

        assert registry.resolve_by_name("jira__create_issue") is not None
        assert context.tool_state.get(approvals.TOOLSET_TOOLS_BY_PATH, {}) == {}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

class TestTheGate:
    async def test_a_read_only_tool_runs(self) -> None:
        assert (await _gate(_context(), "jira__search_issues", jql="x")).decision == PreDecision.ALLOW

    async def test_a_tool_that_changes_data_asks_with_a_card_that_cant_set_rules(self) -> None:
        store = _Store()
        ctx = await _gate(_context(store), "jira__create_issue", title="Bug")

        assert ctx.decision == PreDecision.ASK
        assert ctx.pending_approval is not None
        details = ctx.pending_approval.details
        assert (details["serverName"], details["instanceId"], details["toolName"], details["kind"]) == (
            "Jira (Engineering)", "inst-j", "create_issue", "write",
        )
        assert details["canRemember"] is False
        (saved,) = store.pending()
        assert (saved["source"], saved["instanceId"], saved["arguments"]) == ("toolset", "inst-j", {"title": "Bug"})

    async def test_a_deleting_tool_is_denied_and_the_model_is_told_an_admin_can_allow_it(self) -> None:
        store = _Store()
        ctx = await _gate(_context(store), "jira__delete_issue", key="ENG-1")

        assert ctx.decision == PreDecision.DENY
        reason = ctx.decision_reason or ""
        assert "the organization's tool approval rules" in reason and "an admin can change its rule" in reason
        assert store.pending() == []

    async def test_nobody_watching_blocks_and_says_an_admin_can_pre_approve_it(self) -> None:
        ctx = await _gate(_context(client_name="api"), "jira__create_issue", title="Bug")

        assert ctx.decision == PreDecision.DENY
        reason = ctx.decision_reason or ""
        assert "approve it in the PipesHub app" in reason and "an admin can pre-approve it" in reason

    async def test_a_tool_the_admin_pre_approved_runs_where_nobody_is_watching(self) -> None:
        store = _Store()
        await _set_rules(store, create_issue="allow")
        for overrides in ({"client_name": "api"}, {"is_service_account": True}):
            ctx = await _gate(_context(store, **overrides), "jira__create_issue", title="Bug")
            assert ctx.decision == PreDecision.ALLOW

    async def test_the_instances_rules_apply_to_everyone(self) -> None:
        store = _Store()
        await _set_rules(store, create_issue="allow", delete_issue="ask")
        for user in ("user-1", "user-2"):
            assert (await _gate(_context(store, user_id=user), "jira__create_issue", title="Bug")).decision == PreDecision.ALLOW
            assert (await _gate(_context(store, user_id=user), "jira__delete_issue", key="ENG-1")).decision == PreDecision.ASK

    async def test_an_admins_deny_refuses_even_a_read_only_tool(self) -> None:
        store = _Store()
        await _set_rules(store, search_issues="block")
        ctx = await _gate(_context(store), "jira__search_issues", jql="x")

        assert ctx.decision == PreDecision.DENY
        assert "blocked in the organization's tool approval rules" in (ctx.decision_reason or "")

    async def test_a_persons_own_mcp_rules_dont_apply_to_a_toolset(self) -> None:
        store = _Store()
        await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "allow"}), user_id="user-1", instance_id="inst-j")  # type: ignore[arg-type]
        ctx = await _gate(_context(store), "jira__create_issue", title="Bug")
        assert ctx.decision == PreDecision.ASK

    async def test_rules_that_cant_be_read_refuse_every_tool(self) -> None:
        store = _Store()
        store.reads_fail = True
        ctx = await _gate(_context(store), "jira__search_issues", jql="x")

        assert ctx.decision == PreDecision.DENY
        assert "couldn't be checked" in (ctx.decision_reason or "")

    async def test_a_toolset_whose_instance_isnt_known_starts_from_its_kind(self) -> None:
        store = _Store()
        # Nothing is read: an unreadable store would refuse the call.
        store.reads_fail = True
        context = _context(store)
        assert (await _gate(context, "jira__search_issues", instance=None, jql="x")).decision == PreDecision.ALLOW
        ctx = await _gate(context, "jira__create_issue", instance=None, title="Bug")
        assert ctx.decision == PreDecision.ASK
        assert ctx.pending_approval is not None and ctx.pending_approval.details["serverName"] == "jira"

    async def test_its_switch_lets_toolset_tools_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOOLSET_TOOL_APPROVALS", "false")
        assert (await _gate(_context(), "jira__delete_issue", key="ENG-1")).decision == PreDecision.ALLOW

    async def test_the_mcp_switch_doesnt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TOOL_APPROVALS", "false")
        assert (await _gate(_context(), "jira__delete_issue", key="ENG-1")).decision == PreDecision.DENY


class TestASavedCall:
    def test_one_saved_before_toolsets_were_checked_is_an_mcp_call(self) -> None:
        pending = PendingCall.model_validate({
            "approvalId": "ap-1", "orgId": "org-1", "userId": "user-1", "conversationId": "conv-1",
            "instanceId": "inst-1", "serverName": "Jira", "toolName": "create_issue", "createdAt": 1.0, "expiresAt": 2.0,
        })
        assert pending.source == "mcp"


# ---------------------------------------------------------------------------
# End to end: a request stops on the card; the answer resumes it.
# ---------------------------------------------------------------------------

_QUESTION = "file a bug"
_FILE_IT = ToolCall(id="c1", name="jira__create_issue", arguments={"title": "Login is broken"})
_DONE = ToolCall(id="c2", name="task_complete", arguments={"output": "done"})
_WAITING = "Waiting for your approval to run create_issue on Jira (Engineering)."


def _stopped_turn() -> list[dict[str, Any]]:
    """The conversation as Node sends it after the request that stopped on the card."""
    return [
        {"role": "user_query", "content": _QUESTION},
        {"role": "bot_response", "content": _WAITING, "tool_results": [{
            "tool_id": "c1", "tool_name": "jira__create_issue", "args": {"title": "Login is broken"},
            "result": _WAITING, "status": "error",
        }]},
    ]


async def _ask(store: _Store) -> str:
    context = _context(store)
    asked = await run_request(context, list(_tools(context).values()), _FILE_IT, _DONE)
    assert _Jira.created == []
    assert asked.result.output == _WAITING
    (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
    assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL
    assert blocked["approval"]["canRemember"] is False
    return blocked["approval"]["approvalId"]


async def _answer(
    store: _Store, approval_id: str, decision: str, *, remember: bool = False,
    instance: tuple[str, str] = _INSTANCE,
) -> _Request:
    answer = ToolApprovalAnswer(approval_id=approval_id, decision=decision, remember=remember)  # type: ignore[arg-type]
    context = _context(store, tool_approval=answer)
    return await run_request(
        context, list(_tools(context, instance).values()), _DONE, history=_stopped_turn(),
        query=f"Tool approval: {decision}",
    )


def _no_rules_saved(store: _Store) -> bool:
    return not [key for key in store.values if "tool-rules" in key]


class TestAnsweringTheCard:
    async def test_the_allowed_call_runs_once_and_answers_the_call_the_model_made(self) -> None:
        store = _Store()
        approval_id = await _ask(store)

        answered = await _answer(store, approval_id, "allow")

        assert _Jira.created == ["Login is broken"]
        assert "created Login is broken" in str(_result_of(answered.sent[0]).content)
        (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
        assert (started["approved"], started["approval_answer"]) == (True, "allowed")
        (ended,) = answered.events.of(EventType.TOOL_CALL_END, "c1")
        assert ended["status"] == ToolCallStatus.SUCCESS
        assert answered.result.output == "done"

    @pytest.mark.parametrize("decision", ["allow", "deny"])
    async def test_dont_ask_again_sets_no_rule(self, decision: str) -> None:
        """The card doesn't offer it; a request that sends it anyway changes nothing."""
        store = _Store()
        approval_id = await _ask(store)

        await _answer(store, approval_id, decision, remember=True)

        assert _no_rules_saved(store)
        context = _context(store, conversation_id="conv-9")
        again = await run_request(context, list(_tools(context).values()), _FILE_IT, _DONE)
        (blocked,) = again.events.of(EventType.TOOL_BLOCKED)
        assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL

    async def test_deny_runs_nothing_and_tells_the_model(self) -> None:
        store = _Store()
        approval_id = await _ask(store)

        answered = await _answer(store, approval_id, "deny")

        assert _Jira.created == []
        result = _result_of(answered.sent[0])
        assert "declined" in result.content and result.is_error

    async def test_an_admins_deny_set_since_the_card_was_shown_still_stops_it(self) -> None:
        store = _Store()
        approval_id = await _ask(store)
        await _set_rules(store, create_issue="block")

        answered = await _answer(store, approval_id, "allow")

        assert _Jira.created == []
        assert "blocked in the organization's tool approval rules" in _result_of(answered.sent[0]).content

    async def test_a_tool_the_request_no_longer_has_doesnt_run(self) -> None:
        """The admin replaced the instance between the card and the answer."""
        store = _Store()
        approval_id = await _ask(store)

        answered = await _answer(store, approval_id, "allow", instance=("inst-new", "Jira"))

        assert _Jira.created == []
        assert "no longer available" in _result_of(answered.sent[0]).content

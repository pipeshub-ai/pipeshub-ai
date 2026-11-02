"""Checking an edited tool call before it is approved, and answering a card with an edit or a note."""
from __future__ import annotations

import logging
import time
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall
from app.agent_loop_lib.events.base import EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.tool_approvals import ToolRules, args_hash, check_edit
from app.agents.constants.mcp_server_constants import get_conversation_allow_path
from tests.unit.agents.agent_loop.test_tool_approvals import (
    _DONE,
    _FILE_IT,
    _adapter,
    _answer,
    _ask,
    _context,
    _gate,
    _request,
    _result_of,
    _Server,
    _Store,
    _ToolsetTool,
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"mail_subject": {"type": "string"}, "mail_to": {"type": "array", "items": {"type": "string"}}},
    "additionalProperties": False,
}
_ARGS = {"mail_subject": "Hi", "mail_to": ["a@x.com"]}


def _pending(**over: object) -> approvals.PendingCall:
    base: dict[str, Any] = {
        "approval_id": "a1", "org_id": "o", "user_id": "u", "conversation_id": "c", "source": "toolset",
        "instance_id": "i", "server_name": "Gmail", "tool_name": "send_email", "arguments": dict(_ARGS),
        "tool_path": "/tools/gmail/send_email", "kind": "write", "input_schema": _SCHEMA,
        "created_at": time.time(), "expires_at": time.time() + 60,
    }
    return approvals.PendingCall(**{**base, **over})


def _check(p: approvals.PendingCall, edited: dict[str, Any], rule: Any = None) -> list[approvals.EditError]:  # noqa: ANN401
    return check_edit(p, edited, shown_hash=args_hash(p.arguments), own_rule=rule)


def test_unchanged_arguments_pass() -> None:
    assert _check(_pending(), dict(_ARGS)) == []


def test_stale_card_refused() -> None:
    p = _pending()
    assert check_edit(p, dict(_ARGS), shown_hash="0" * 64, own_rule=None)[0].message == "This request changed. Reload."


def test_schema_type_error_points_at_field() -> None:
    assert _check(_pending(), {**_ARGS, "mail_subject": 5})[0].path == "/mail_subject"


def test_nested_path_is_a_pointer() -> None:
    assert _check(_pending(), {**_ARGS, "mail_to": ["a", 3]})[0].path == "/mail_to/1"


def test_unknown_key_refused_when_additional_properties_false() -> None:
    assert _check(_pending(), {**_ARGS, "bcc": "x"}) != []


def _pattern_call(pattern: str, value: str) -> approvals.PendingCall:
    schema = {"type": "object", "properties": {"q": {"type": "string", "pattern": pattern}}}
    return _pending(arguments={"q": value}, input_schema=schema)


def test_pattern_mismatch_refused() -> None:
    p = _pattern_call("^[a-z]+$", "abc")
    assert _check(p, {"q": "ABC"})[0].path == "/q"


@pytest.mark.parametrize("pattern", ["^(a+)+$", "^(a|a)+$", "^(a|aa)+$"])
def test_catastrophic_pattern_is_bounded(pattern: str) -> None:
    # A server's regex can backtrack for ages; the check gives up within its budget, and a value
    # it could not check is left to the tool to reject.
    p = _pattern_call(pattern, "a")
    start = time.monotonic()
    errors = _check(p, {"q": "a" * 40 + "!"})
    assert time.monotonic() - start < 1
    assert all(e.path == "/q" for e in errors)


def test_one_budget_covers_every_pattern_and_skips_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    fields = [f"q{i}" for i in range(4)]
    schema = {"type": "object", "properties": {f: {"type": "string", "pattern": "^(a|aa)+$"} for f in fields}}
    p = _pending(arguments=dict.fromkeys(fields, "a"), input_schema=schema)
    value = "a" * 40 + "!"
    start = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=approvals.__name__):
        # Distinct strings, as parsed JSON gives: the log finds each field by its value's identity.
        assert _check(p, {f: f"{value}{f}" for f in fields}) == []
    # Four timeouts would take four budgets; one shared budget ends them all.
    assert time.monotonic() - start < approvals._PATTERN_BUDGET_SECONDS * 2 + 0.3
    for f in fields:
        assert f"/{f}" in caplog.text
    assert "/tools/gmail/send_email" in caplog.text
    assert value not in caplog.text


_KEY = "a" * 40 + "!"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "patternProperties": {"^(a+)+$": {"type": "string"}}},
        {"type": "object", "patternProperties": {"^(a+)+$": {"type": "string"}}, "additionalProperties": False},
        {"type": "object", "patternProperties": {"^(a+)+$": {"type": "string"}}, "unevaluatedProperties": False},
    ],
    ids=["patternProperties", "additionalProperties", "unevaluatedProperties"],
)
def test_catastrophic_key_pattern_is_bounded(schema: dict[str, Any]) -> None:
    # The keys of an edit are matched against the server's patterns too.
    p = _pending(arguments={}, input_schema=schema)
    start = time.monotonic()
    _check(p, {_KEY: "x"})
    assert time.monotonic() - start < 1


def test_key_patterns_still_apply() -> None:
    schema = {"type": "object", "patternProperties": {"^n_": {"type": "integer"}}, "additionalProperties": False}
    p = _pending(arguments={"n_a": 1}, input_schema=schema)
    assert _check(p, {"n_a": 2}) == []
    assert _check(p, {"n_a": "x"})[0].path == "/n_a"
    assert _check(p, {"other": 1}) != []


def test_ref_and_anyof_schema_followed() -> None:
    schema = {
        "type": "object", "$defs": {"id": {"type": "integer"}},
        "properties": {"v": {"anyOf": [{"$ref": "#/$defs/id"}, {"type": "null"}]}},
    }
    p = _pending(input_schema=schema, arguments={"v": 1})
    assert _check(p, {"v": None}) == []
    assert _check(p, {"v": "x"}) != []


def _sql_call(query: str) -> approvals.PendingCall:
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    return _pending(tool_path="/tools/mariadb/execute_query", arguments={"query": query}, input_schema=schema)


def test_sql_edited_to_drop_refused() -> None:
    assert "riskier" in _check(_sql_call("SELECT 1"), {"query": "DROP TABLE t"})[0].message


def test_edit_within_the_same_kind_passes() -> None:
    assert _check(_sql_call("SELECT 1"), {"query": "SELECT 2"}) == []


def test_unresolvable_ref_in_schema_is_not_editable() -> None:
    p = _pending(input_schema={"type": "object", "properties": {"a": {"$ref": "#/$defs/missing"}}}, arguments={"a": 1})
    assert _check(p, {"a": 2})[0].message == "This action can't be edited."


def test_malformed_schema_is_not_editable() -> None:
    assert _check(_pending(input_schema={"type": "nonsense"}), dict(_ARGS))[0].message == "This action can't be edited."


def test_rule_that_now_blocks_refused() -> None:
    assert _check(_pending(), dict(_ARGS), "block")[0].message == "A rule blocks this action with these details."


def test_missing_schema_refused() -> None:
    assert _check(_pending(input_schema=None), dict(_ARGS))[0].message == "This action can't be edited."


def test_record_saved_before_edits_refused() -> None:
    assert _check(_pending(tool_path=""), dict(_ARGS))[0].message == "This action can't be edited."


def test_call_kind_unchanged_for_existing_callers() -> None:
    target = approvals.ApprovalTarget(source="mcp", instance_id="i", tool_name="t", owner="o", kind="write", tool=None)  # type: ignore[arg-type]
    for path, args in (("/tools/mariadb/execute_query", {"query": "DROP TABLE t"}), ("/x", {})):
        assert approvals.call_kind(target, path, args) == approvals.kind_of_call("write", path, args)


def test_args_hash_survives_a_value_json_cannot_encode() -> None:
    assert len(args_hash({"when": {1, 2}})) == 64


# ---------------------------------------------------------------------------
# Answering a card with edited arguments, or declining it with a note
# ---------------------------------------------------------------------------

_ASKED = {"title": "Login is broken"}
_NOTHING_RUNS = "Nothing runs until the user fixes the approval card."
_HASH = args_hash(_ASKED)


def _claimed(store: _Store) -> list[str]:
    return [k for k in store.values if "/claims/" in k]


async def _edited(store: _Store, mcp: _Server, approval_id: str, arguments: dict[str, Any], **fields: Any):  # noqa: ANN202, ANN401
    return await _answer(
        store, mcp, approval_id, "allow", answer_fields={"arguments": arguments, "args_hash": _HASH, **fields},
    )


async def test_edited_allow_runs_with_edited_arguments() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    answered = await _edited(store, mcp, approval_id, {"title": "Signin is broken"})
    assert mcp.calls == [{"tool": "create_issue", "title": "Signin is broken"}]
    (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
    assert (started["edited"], started["args"]) == (True, {"title": "Signin is broken"})
    assert started["changed_fields"] == ["title"]
    claim = store.values[_claimed(store)[0]]["outcome"]
    assert claim["editedArguments"] == {"title": "Signin is broken"}
    assert approvals.PendingCall.model_validate(store.pending()[0]).arguments == _ASKED


async def test_model_is_told_which_fields_changed() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    answered = await _edited(store, mcp, approval_id, {"title": "Signin is broken", "labels": ["auth"]})
    text = _result_of(answered.sent[0]).content
    assert text.startswith("The user changed `labels`, `title` before approving.")
    assert text.endswith("created Signin is broken")


async def test_history_call_rewritten_to_edited_arguments() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    answered = await _edited(store, mcp, approval_id, {"title": "Signin is broken"})
    calls = [c for m in answered.sent[0] if isinstance(m, AssistantMessage) for c in m.tool_calls or [] if c.id == "c1"]
    assert [c.arguments for c in calls] == [{"title": "Signin is broken"}]


async def test_unchanged_arguments_are_a_plain_allow() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    answered = await _edited(store, mcp, approval_id, dict(_ASKED))
    assert _result_of(answered.sent[0]).content == "created Login is broken"
    (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
    assert started.get("edited") is None
    assert store.values[_claimed(store)[0]]["outcome"]["editedArguments"] is None


async def test_refused_edit_keeps_card_open_and_runs_nothing() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    # A model that ignores the instruction and calls the tool again still runs nothing.
    again = ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Signin is broken"})
    answered = await _answer(
        store, mcp, approval_id, "allow", again, _DONE, answer_fields={"arguments": {"title": 5}, "args_hash": _HASH},
    )
    assert mcp.calls == [] and _claimed(store) == []
    assert _result_of(answered.sent[1], "c3").content == _NOTHING_RUNS
    # No second card or saved call for the repeat.
    assert answered.events.of(EventType.TOOL_BLOCKED, "c3")[0]["status"] == ToolCallStatus.BLOCKED
    assert len(store.pending()) == 1
    (card,) = answered.events.of(EventType.TOOL_BLOCKED, "c1")
    assert card["status"] == ToolCallStatus.AWAITING_APPROVAL
    assert card["approval"]["approvalId"] == approval_id
    assert card["approval"]["editErrors"][0]["path"] == "/title"
    # The card reopens on the person's edit, not on the call as asked.
    assert card["approval"]["editedArguments"] == {"title": 5}
    assert answered.events.of(EventType.TOOL_CALL_START, "c1") == []
    # The model is only told to point the person at the card; the call keeps waiting for them.
    assert any("highlighted" in c for c in answered.goal.constraints)
    # The card can still be answered.
    await _edited(store, mcp, approval_id, {"title": "Signin is broken"})
    assert mcp.calls == [{"tool": "create_issue", "title": "Signin is broken"}]


async def test_refused_edit_turn_runs_no_allow_rule_tool() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    await approvals.save_own_rules(store, ToolRules(tools={"create_issue": "allow"}), user_id="user-1", instance_id="inst-1")  # type: ignore[arg-type]
    pre_approved = ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Signin is broken"})
    answered = await _answer(
        store, mcp, approval_id, "allow", pre_approved, _DONE,
        answer_fields={"arguments": {"title": 5}, "args_hash": _HASH},
    )
    assert mcp.calls == [] and _claimed(store) == []
    assert _result_of(answered.sent[1], "c3").content == _NOTHING_RUNS


async def test_stale_card_edit_is_refused() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    answered = await _edited(store, mcp, approval_id, {"title": "x"}, args_hash="0" * 64)
    (card,) = answered.events.of(EventType.TOOL_BLOCKED, "c1")
    assert card["approval"]["editErrors"] == [{"path": "", "message": "This request changed. Reload."}]
    assert mcp.calls == [] and _claimed(store) == []


async def test_edits_turned_off_refuse_edited_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    monkeypatch.setenv("TOOL_APPROVAL_EDITS", "false")
    await _edited(store, mcp, approval_id, {"title": "x"})
    assert mcp.calls == [] and _claimed(store) == []


async def test_edit_from_another_user_isnt_checked_or_shown() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    answered = await _answer(
        store, mcp, approval_id, "allow", answer_fields={"arguments": {"title": 5}, "args_hash": _HASH}, user_id="user-2",
    )
    assert answered.events.of(EventType.TOOL_BLOCKED, "c1") == []
    assert mcp.calls == [] and _claimed(store) == []


async def test_store_failure_while_checking_an_edit_runs_nothing() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    store.reads_fail = True
    answered = await _edited(store, mcp, approval_id, {"title": "Signin is broken"})
    store.reads_fail = False
    assert mcp.calls == [] and _claimed(store) == []
    assert "answer the card again" in _result_of(answered.sent[0]).content


async def test_store_failure_while_checking_an_edit_raises_no_second_card(monkeypatch: pytest.MonkeyPatch) -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)

    async def _down(*_args: Any) -> None:  # noqa: ANN401
        raise ConnectionError("the store is down")

    monkeypatch.setattr(approvals, "read_pending", _down)
    again = ToolCall(id="c3", name="mcp_jira_create_issue", arguments={"title": "Signin is broken"})
    answered = await _answer(
        store, mcp, approval_id, "allow", again, _DONE, answer_fields={"arguments": {"title": "x"}, "args_hash": _HASH},
    )
    assert mcp.calls == [] and _claimed(store) == [] and len(store.pending()) == 1
    assert _result_of(answered.sent[1], "c3").content == _NOTHING_RUNS


async def test_second_answer_after_edited_allow_shows_first_outcome() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    await _edited(store, mcp, approval_id, {"title": "Signin is broken"})
    again = await _edited(store, mcp, approval_id, {"title": 5})
    assert len(mcp.calls) == 1
    assert again.events.of(EventType.TOOL_BLOCKED, "c1") == []
    assert _result_of(again.sent[0]).content.startswith("The user changed `title` before approving.")
    (started,) = again.events.of(EventType.TOOL_CALL_START, "c1")
    assert (started["edited"], started["args"]) == (True, {"title": "Signin is broken"})


async def test_deny_with_feedback_tells_the_model() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    answered = await _answer(store, mcp, approval_id, "deny", answer_fields={"feedback": "Use the team list"})
    assert mcp.calls == []
    assert _result_of(answered.sent[0]).content == (
        "The user declined this call and said: Use the team list. Follow their instruction; don't repeat the same call."
    )
    (ended,) = answered.events.of(EventType.TOOL_CALL_END, "c1")
    assert ended["content"] == "Declined: Use the team list"
    (started,) = answered.events.of(EventType.TOOL_CALL_START, "c1")
    assert started["approval_note"] == "Use the team list"


def test_answer_rejects_arguments_with_deny() -> None:
    with pytest.raises(ValidationError):
        approvals.ToolApprovalAnswer(approval_id="a", decision="deny", arguments={})


def test_answer_rejects_arguments_with_remember() -> None:
    with pytest.raises(ValidationError):
        approvals.ToolApprovalAnswer(approval_id="a", decision="allow", remember=True, arguments={})


def test_answer_rejects_feedback_with_allow() -> None:
    with pytest.raises(ValidationError):
        approvals.ToolApprovalAnswer(approval_id="a", decision="allow", feedback="no")


def test_answer_rejects_feedback_over_2000_chars() -> None:
    with pytest.raises(ValidationError):
        approvals.ToolApprovalAnswer(approval_id="a", decision="deny", feedback="x" * 2001)


def test_answer_rejects_arguments_over_64000_chars() -> None:
    with pytest.raises(ValidationError):
        approvals.ToolApprovalAnswer(approval_id="a", decision="allow", arguments={"v": "x" * 64_000})


def test_answer_reads_the_wire_names() -> None:
    answer = approvals.ToolApprovalAnswer.model_validate(
        {"approvalId": "a", "decision": "allow", "argsHash": _HASH, "arguments": {"title": "x"}},
    )
    assert (answer.args_hash, answer.arguments) == (_HASH, {"title": "x"})


# ---------------------------------------------------------------------------
# Allow for this conversation
# ---------------------------------------------------------------------------

_SQL_PATH = "/tools/mariadb/execute_query"
_SQL_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}


async def _allowed_in_conversation(store: _Store, mcp: _Server) -> None:
    approval_id = await _ask(store, mcp)
    await _answer(store, mcp, approval_id, "allow", answer_fields={"remember_for_conversation": True})
    assert len(mcp.calls) == 1


async def test_allowed_for_conversation_runs_the_next_call_without_a_card() -> None:
    store, mcp = _Store(), _Server()
    await _allowed_in_conversation(store, mcp)
    path = get_conversation_allow_path("conv-1", "mcp", "inst-1", "create_issue")
    assert set(store.values[path]) == {"userId", "schemaHash", "kind"} and store.ttls[path] == approvals._RECORD_TTL_SECONDS
    again = await _request(_context(store), mcp, _FILE_IT, _DONE)
    assert len(mcp.calls) == 2 and again.events.of(EventType.TOOL_BLOCKED) == []


async def test_a_declined_or_blocked_run_saves_no_allow() -> None:
    store, mcp = _Store(), _Server()
    approval_id = await _ask(store, mcp)
    await _answer(store, mcp, approval_id, "deny")
    assert not [k for k in store.values if "conversation-allows" in k]


async def test_another_conversation_still_asks() -> None:
    store, mcp = _Store(), _Server()
    await _allowed_in_conversation(store, mcp)
    await _request(_context(store, conversation_id="conv-2"), mcp, _FILE_IT, _DONE)
    assert len(mcp.calls) == 1


async def test_another_user_in_the_conversation_still_asks() -> None:
    store, mcp = _Store(), _Server()
    await _allowed_in_conversation(store, mcp)
    await _request(_context(store, user_id="user-2"), mcp, _FILE_IT, _DONE)
    assert len(mcp.calls) == 1


async def test_a_changed_schema_asks_again() -> None:
    store = _Store()
    adapter = _adapter(read_only=False)
    path = get_conversation_allow_path("conv-1", "mcp", "inst-1", "create_issue")
    store.values[path] = {"userId": "user-1", "schemaHash": args_hash({"type": "object"}), "kind": "write"}
    assert (await _gate(_context(store), adapter)).decision == PreDecision.ALLOW
    store.values[path]["schemaHash"] = args_hash({"type": "object", "properties": {}})
    assert (await _gate(_context(store), adapter)).decision == PreDecision.ASK


async def test_an_unreadable_allow_asks() -> None:
    store = _Store()
    store.values[get_conversation_allow_path("conv-1", "mcp", "inst-1", "create_issue")] = {
        "userId": "user-1", "schemaHash": args_hash({"type": "object"}), "kind": "write",
    }
    context = _context(store)
    real_get = store.get_config

    async def _get(key: str, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        if "conversation-allows" in key:
            raise ConnectionError("the store is down")
        return await real_get(key, *a, **k)

    store.get_config = _get  # type: ignore[method-assign]
    assert (await _gate(context, _adapter(read_only=False))).decision == PreDecision.ASK


async def _sql_gate(store: _Store, query: str, *, rule: Any = None) -> Any:  # noqa: ANN401
    context = _context(store)
    target = approvals.ApprovalTarget(
        source="toolset", instance_id="db-1", tool_name="execute_query", owner="MariaDB", kind="write",
        tool=_ToolsetTool(_SQL_SCHEMA), defaults="mcp", personal=True,  # type: ignore[arg-type]
    )
    context.tool_state.setdefault(approvals.TOOLSET_TOOLS_BY_PATH, {})[_SQL_PATH] = target
    if rule:
        await approvals.save_instance_rules(store, ToolRules(tools={"execute_query": rule}), instance_id="db-1")  # type: ignore[arg-type]
    ctx = ToolCallContext(tool_path=_SQL_PATH, tool_input={"query": query})

    async def _next() -> None:
        return None

    await approvals.approval_gate(context)(ctx, _next)
    return ctx


async def test_a_riskier_call_asks_again() -> None:
    store = _Store()
    store.values[get_conversation_allow_path("conv-1", "toolset", "db-1", "execute_query")] = {
        "userId": "user-1", "schemaHash": args_hash(_SQL_SCHEMA), "kind": "write",
    }
    assert (await _sql_gate(store, "SELECT 1", rule="ask")).decision == PreDecision.ALLOW
    assert (await _sql_gate(store, "DROP TABLE t", rule="ask")).decision == PreDecision.ASK


async def test_a_block_rule_still_blocks() -> None:
    store = _Store()
    store.values[get_conversation_allow_path("conv-1", "toolset", "db-1", "execute_query")] = {
        "userId": "user-1", "schemaHash": args_hash(_SQL_SCHEMA), "kind": "write",
    }
    assert (await _sql_gate(store, "SELECT 1", rule="block")).decision == PreDecision.DENY


def test_answer_rejects_conversation_allow_with_deny_remember_or_arguments() -> None:
    for extra in ({"decision": "deny"}, {"remember": True}, {"arguments": {}}):
        with pytest.raises(ValidationError):
            approvals.ToolApprovalAnswer(**{"approval_id": "a", "decision": "allow", "remember_for_conversation": True, **extra})


def test_answer_reads_remember_for_conversation_from_the_wire() -> None:
    answer = approvals.ToolApprovalAnswer.model_validate({"approvalId": "a", "decision": "allow", "rememberForConversation": True})
    assert answer.remember_for_conversation is True

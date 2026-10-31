"""Checking an edited tool call before it is approved."""
from __future__ import annotations

import time
from typing import Any

from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.tool_approvals import args_hash, check_edit

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


def test_ref_and_anyof_schema_followed() -> None:
    schema = {
        "type": "object", "$defs": {"id": {"type": "integer"}},
        "properties": {"v": {"anyOf": [{"$ref": "#/$defs/id"}, {"type": "null"}]}},
    }
    p = _pending(input_schema=schema, arguments={"v": 1})
    assert _check(p, {"v": None}) == []
    assert _check(p, {"v": "x"}) != []


def test_sql_edited_to_drop_refused() -> None:
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    p = _pending(tool_path="/tools/mariadb/execute_query", arguments={"query": "UPDATE t SET a=1"}, input_schema=schema)
    assert "riskier" in _check(p, {"query": "DROP TABLE t"})[0].message


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

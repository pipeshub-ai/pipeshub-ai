"""AP-06 (C3.75): which calls a person may edit before approving, how the edit is read back from the journal, and
that every hold that forwards a denial's reason forwards an edit too (a wrapper that dropped it would run the
model's arguments after the person approved their own)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from harness_core.contracts import canonical_json
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import flow
from harness_core.effects.edits import (
    MAX_EDIT_NOTE_CHARS,
    MAX_EDITABLE_ARGS_BYTES,
    approved_edit,
    edit_note,
    noted,
    offering_edit,
    offers_edit,
)
from harness_core.effects.flow import reason_for_journal
from harness_core.effects.types import ToolEffect, ToolExecution
from harness_core.journal.inline import content_digest, inline_ref
from harness_core.kernel.core.messages import ToolCall

HARNESS_CORE = Path(__file__).resolve().parents[4] / "harness_core"
WRITE = ToolEffect("mail@default/send", "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
EXECUTE = ToolEffect("code@default/run", "execute", True, SinkDescriptor(egress="none", args_leave_boundary=False))
FLOW = {"rule": "flow_external_write", "toolId": WRITE.tool_id, "conversationGrant": False}



# An edition's ask rule (``register_edition_rule``): its call is decided as asked, never edited first.
EDITION_RULE = "edition_rule"


@pytest.fixture(autouse=True)
def _edition_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(flow._EDITION_RULES, EDITION_RULE, "edition_asks")


def _call(**arguments: object) -> ToolCall:
    return ToolCall(id="c1", name="send", arguments=arguments)


def test_ap06_a_write_with_arguments_is_editable() -> None:
    assert offers_edit(_call(to="a@b.c"), WRITE, FLOW)
    assert offering_edit(_call(to="a@b.c"), WRITE, FLOW) == {**FLOW, "editable": True}


@pytest.mark.parametrize(
    ("call", "effect", "reason"),
    [
        (_call(), WRITE, FLOW),
        (_call(code="print(1)"), EXECUTE, FLOW),
        (_call(to="a@b.c"), WRITE, {**FLOW, "rule": EDITION_RULE}),
        (_call(body="x" * MAX_EDITABLE_ARGS_BYTES), WRITE, FLOW),
    ],
    ids=["no-arguments", "code", "edition-rule", "too-large"],
)
def test_ap06_what_is_never_editable(call: ToolCall, effect: ToolEffect, reason: dict[str, object]) -> None:
    assert not offers_edit(call, effect, reason)  # type: ignore[arg-type]
    assert "editable" not in offering_edit(call, effect, reason)  # type: ignore[arg-type]


@pytest.mark.parametrize("edits", [True, False])
def test_ap06_the_offer_reaches_only_a_journal_that_takes_edits(edits: bool) -> None:
    journaled = reason_for_journal({**FLOW, "editable": True}, layers=True, edits=edits)
    assert journaled is not None and ("editable" in journaled) is edits


def test_ap06_an_edit_reads_back_with_its_hash() -> None:
    arguments = {"to": "team@acme.com", "body": "Summary only"}
    ref = inline_ref(canonical_json(arguments))
    edit = approved_edit(ref)
    assert edit is not None and edit.arguments == arguments and edit.digest == content_digest(canonical_json(arguments))
    assert approved_edit(None) is None


@pytest.mark.parametrize(
    "ref",
    [
        inline_ref(b"[1, 2]"),
        inline_ref(b"not json"),
        inline_ref(canonical_json({"to": "x"})).model_copy(update={"hash": content_digest(b"tampered")}),
        inline_ref(b"{}").model_copy(update={"ref": "sealed:abc"}),
    ],
    ids=["not-an-object", "not-json", "hash-mismatch", "not-inline"],
)
def test_ap06_an_edit_that_cannot_be_read_is_never_empty(ref: object) -> None:
    edit = approved_edit(ref)  # type: ignore[arg-type]
    assert edit is not None and edit.arguments is None


def test_ap06_the_model_reads_what_ran_ahead_of_the_result() -> None:
    arguments = {"to": "team@acme.com"}
    text = noted(ToolExecution(status="ok", content="sent"), arguments).content
    assert isinstance(text, str) and text.startswith(edit_note(arguments)) and text.endswith("sent")
    parts = noted(ToolExecution(status="ok", content=[{"type": "text", "text": "ok"}], parts=True), arguments).content
    assert isinstance(parts, list) and parts[0] == {"type": "text", "text": edit_note(arguments)}
    structured = noted(ToolExecution(status="ok", content={"id": 7}), arguments).content
    assert isinstance(structured, str) and structured.endswith(json.dumps({"id": 7}))
    assert len(edit_note({"body": "x" * (MAX_EDIT_NOTE_CHARS * 2)})) < MAX_EDIT_NOTE_CHARS + 200


def _methods(node: ast.ClassDef) -> set[str]:
    return {item.name for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_ap06_every_hold_that_forwards_a_denial_forwards_an_edit() -> None:
    holds: list[str] = []
    missing: list[str] = []
    for path in HARNESS_CORE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = _methods(node)
            protocol = any(isinstance(b, ast.Name) and b.id == "Protocol" for b in node.bases)
            if "denial_reason" not in methods or protocol:
                continue
            holds.append(node.name)
            if "approved_edit" not in methods:
                missing.append(f"{path.relative_to(HARNESS_CORE)}::{node.name}")
    assert {"BridgeApprovalHold", "BoundedHold", "_PresentingHold"} <= set(holds)
    assert missing == []

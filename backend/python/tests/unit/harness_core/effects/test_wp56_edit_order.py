"""WP-56 (AP-06, C3.75): the edit card lists a held call's values in the tool's own order. The worker knows the tool's
input schema; the card does not, so the offer to edit carries the schema's property order (``fieldOrder``), and it
reaches only a journal that accepts it (``approval_edit_order``), as ``editable`` reaches only one that takes edits.

WP56-ORDER-1 the offer names the schema's property order, bounded; no schema, no properties or no offer: none.
WP56-ORDER-2 the order reaches only a journal that accepts it, and never without the edit offer.
"""

from __future__ import annotations

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import flow
from harness_core.effects.edits import MAX_FIELD_ORDER, offering_edit
from harness_core.effects.flow import reason_for_journal
from harness_core.effects.types import ToolEffect
from harness_core.kernel.core.messages import ToolCall

WRITE = ToolEffect("mail@default/send", "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
READ = ToolEffect("mail@default/find", "read", True, SinkDescriptor(egress="self", args_leave_boundary=False))
FLOW = {"rule": "flow_external_write", "toolId": WRITE.tool_id, "conversationGrant": False}
SCHEMA = {"type": "object", "properties": {"to": {"type": "string"}, "subject": {}, "body": {}}, "required": ["to"]}
CALL = ToolCall(id="c1", name="send", arguments={"body": "Hi all", "subject": "Q3", "to": "all@acme.com"})



# An edition's ask rule (``register_edition_rule``): its call is decided as asked, never edited first.
EDITION_RULE = "edition_rule"


@pytest.fixture(autouse=True)
def _edition_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(flow._EDITION_RULES, EDITION_RULE, "edition_asks")


def test_wp56_order_1_the_offer_names_the_schemas_property_order() -> None:
    offered = offering_edit(CALL, WRITE, FLOW, SCHEMA)
    assert offered == {**FLOW, "editable": True, "fieldOrder": ["to", "subject", "body"]}


@pytest.mark.parametrize(
    "schema",
    [None, {"type": "object"}, {"properties": "nope"}, {"properties": {}}, "not a schema"],
    ids=["none", "no-properties", "properties-not-an-object", "empty", "not-an-object"],
)
def test_wp56_order_1_without_an_order_to_name_the_offer_names_none(schema: object) -> None:
    assert offering_edit(CALL, WRITE, FLOW, schema) == {**FLOW, "editable": True}  # type: ignore[arg-type]


def test_wp56_order_1_a_call_that_is_not_editable_names_no_order() -> None:
    assert "fieldOrder" not in offering_edit(CALL, READ, FLOW, SCHEMA)
    assert "fieldOrder" not in offering_edit(CALL, WRITE, {**FLOW, "rule": EDITION_RULE}, SCHEMA)


def test_wp56_order_1_the_order_is_bounded_and_names_only_names_the_contract_takes() -> None:
    names = [f"f{i}" for i in range(MAX_FIELD_ORDER + 10)]
    schema = {"properties": {"x" * 129: {}, "": {}, **{n: {} for n in names}}}
    order = offering_edit(CALL, WRITE, FLOW, schema)["fieldOrder"]
    assert order == names[:MAX_FIELD_ORDER]


@pytest.mark.parametrize(("edits", "order", "kept"), [(True, True, True), (True, False, False), (False, True, False)])
def test_wp56_order_2_the_order_reaches_only_a_journal_that_takes_it_with_the_offer(edits: bool, order: bool, kept: bool) -> None:
    reason = {**FLOW, "editable": True, "fieldOrder": ["to", "subject", "body"]}
    journaled = reason_for_journal(reason, layers=True, edits=edits, edit_order=order)
    assert journaled is not None
    assert ("fieldOrder" in journaled) is kept
    assert ("editable" in journaled) is edits

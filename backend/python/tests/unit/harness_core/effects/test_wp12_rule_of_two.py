"""KN-08 (C3.23): the ``untrusted_content_read`` taint feeds the flow rule as the Rule of Two (design-plan §4.9).

A run whose context is private and which read content an outsider could write asks before any call that sends data
out, whatever the rule's threshold: that content may be what asked for the send. Without the untrusted read the
threshold decides as before, and a public context never asks.
"""

from __future__ import annotations

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import FlowRule, GrantingLedger, Sensitivity
from harness_core.effects.types import ToolEffect
from harness_core.engines.durable.ledger import ItemLabelLedger
from harness_core.journal.types import JournalEvent
from harness_core.security.labels import ReadsUntrusted

SEND = ToolEffect("gmail@default/send", "write", True, SinkDescriptor(egress="external", args_leave_boundary=True))
INTERNAL = ToolEffect("jira@default/create", "write", True, SinkDescriptor(egress="internal_audience", args_leave_boundary=False))


@pytest.mark.parametrize(
    ("above", "sensitivity", "untrusted", "decision"),
    [
        ("private", "private", False, "allow"),
        ("private", "private", True, "ask"),
        ("private", "unknown", False, "ask"),
        ("private", "public", True, "allow"),
        ("public", "private", False, "ask"),
        ("public", "private", True, "ask"),
    ],
)
def test_kn08_rule_of_two(above: str, sensitivity: Sensitivity, untrusted: bool, decision: str) -> None:
    assert FlowRule("ask", above=above).decide(sensitivity, SEND, untrusted=untrusted) == decision  # type: ignore[arg-type]


def test_kn08_a_deny_rule_still_asks_under_the_rule_of_two_and_nothing_inside_the_org_is_held() -> None:
    assert FlowRule("deny", above="private").decide("private", SEND, untrusted=True) == "ask"
    assert FlowRule("deny", above="private").decide("unknown", SEND, untrusted=True) == "deny"
    assert FlowRule("ask", above="private").decide("private", INTERNAL, untrusted=True) == "allow"


def _outcome(seq: int, integrity: str) -> JournalEvent:
    return JournalEvent(
        org_id="o", session_id="s", stream_id="s", seq=seq, run_id="r", type="tool.outcome", critical=True,
        ts="2026-10-27T00:00:00Z", writer="worker",
        data={"invocationId": f"i{seq}", "status": "ok", "labels": {"integrity": integrity, "confidentiality": {"sources": []}}},
    )


def test_kn08_the_reference_ledger_is_tainted_by_an_external_outcome_and_the_granting_ledger_says_so() -> None:
    ledger = ItemLabelLedger()
    granting = GrantingLedger(ledger)
    assert isinstance(ledger, ReadsUntrusted) and isinstance(granting, ReadsUntrusted)
    granting.apply([_outcome(1, "internal")])
    assert not ledger.untrusted_read("r") and not granting.untrusted_read("r")
    granting.apply([_outcome(2, "external")])
    assert ledger.untrusted_read("r") and granting.untrusted_read("r")

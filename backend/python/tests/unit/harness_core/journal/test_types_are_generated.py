"""CPD0-20/25: the journal's value types are the contract's, not copies of it.

R3 M15 found these hand-copied, and drifted: the worker's ``YieldReason`` lacked Node's
``payload_store_unavailable``. The envelope stays a hand-written model (it carries defaults and the
wire mapping), so its fields are checked against the generated envelope instead.
"""

from __future__ import annotations

import re
from typing import get_args

from harness_core.contracts.generated import enums
from harness_core.contracts.generated.envelope import (
    ClaimCheckedEnvelope,
    InlineEnvelope,
)
from harness_core.journal import types


def test_value_types_are_the_generated_ones() -> None:
    for name in (
        "Writer", "Visibility", "RunStatus", "SuspendReason", "YieldReason",
        "CompletedOutcome", "InteractionDecision", "InteractionChannel",
    ):
        assert getattr(types, name) is getattr(enums, name), name


def test_yield_reasons_include_the_node_authored_one() -> None:
    assert "payload_store_unavailable" in get_args(types.YieldReason)


def test_terminal_statuses_are_run_statuses() -> None:
    assert types.TERMINAL_STATUSES < set(enums.RUN_STATUSES)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def test_journal_event_has_exactly_the_envelope_s_fields() -> None:
    inline = set(InlineEnvelope.model_fields)
    claim_checked = set(ClaimCheckedEnvelope.model_fields)
    assert inline - {"data"} == claim_checked - {"payload_ref"}
    assert set(types.JournalEvent.model_fields) == inline


def test_journal_event_round_trips_the_wire_names() -> None:
    wire = {
        "orgId": "o1", "sessionId": "s1", "streamId": "s1", "seq": 3, "runId": "r1", "attemptId": "a1",
        "fence": 2, "type": "run.claimed", "schemaVersion": 1, "critical": True, "ts": "2026-10-09T00:00:00Z",
        "writer": "node", "visibility": "owner", "data": {},
    }
    event = types.JournalEvent.from_envelope(wire)
    assert {_snake(k) for k in wire} == set(types.JournalEvent.model_fields) - {"labels"}
    InlineEnvelope.model_validate(event.envelope())

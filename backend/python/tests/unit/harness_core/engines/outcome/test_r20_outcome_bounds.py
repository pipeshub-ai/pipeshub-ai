"""R20-BOUND: the settled outcome a run journals always fits the event's contract.

A research run: the engine kept more than 500 notes, ``delivered.notes`` carried all of them, and Node
refused the whole completion commit (``JOURNAL_INVALID_EVENT``: "data.outcome.delivered.notes Array must contain at
most 500 element(s)"), so a run whose engine had finished ended failed. The worker now bounds every listed field to
the schema's limits, keeping the notes a "continue" reads first, and these cases check its output against the
contract itself.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness_core.engines.outcome.ledger import ArtifactWritten
from harness_core.engines.outcome.outcome import (
    Completed,
    Delivered,
    Failed,
    Partial,
    outcome_wire,
    settled_event,
)
from tests.cohesion.support import REPO_ROOT
from tests.unit.contracts_harness.support import registry

SCHEMA = REPO_ROOT / "contracts" / "harness" / "ext" / "pipeshub" / "events" / "x.pipeshub.outcome.settled.schema.json"


def _validator() -> Draft202012Validator:
    """The event's data definition, its $refs resolved from the contract tree (never fetched)."""
    schema_id = json.loads(SCHEMA.read_text(encoding="utf-8"))["$id"]
    return Draft202012Validator({"$ref": f"{schema_id}#/$defs/PipeshubOutcomeSettledData"}, registry=registry())


def _data(event: Any) -> dict[str, Any]:  # noqa: ANN401 - a NewEvent
    return json.loads(json.dumps(event.data))


def _deliverable() -> Any:  # noqa: ANN401
    from harness_core.engines.outcome.deliverable import resolve_deliverable

    return resolve_deliverable("Write the report to out/report.md")


NOTES = (*(f"notes/web/page-{i:05d}.json" for i in range(1_200)), "notes/handoff.json", "notes/partial-answer.md")


@pytest.mark.parametrize(
    "outcome",
    [
        Completed(Delivered(notes=NOTES)),
        Partial("time", Delivered(file=ArtifactWritten("out/report.md", 9, 140_000), notes=NOTES), tuple(f"part-{i}" * 40 for i in range(30))),
        Failed("x" * 500, Delivered(notes=tuple("n/" + "y" * 5_000 for _ in range(3)))),
    ],
    ids=["completed-1202-notes", "partial-30-missing", "failed-long-reason-and-paths"],
)
def test_r20_bound_1_every_settled_outcome_validates_against_the_contract(outcome: Any) -> None:  # noqa: ANN401
    event = settled_event(outcome, _deliverable(), closing="c" * 20_000, opening=True)
    errors = sorted(_validator().iter_errors(_data(event)), key=lambda e: list(e.path))
    assert not errors, [e.message for e in errors]


def test_r20_bound_2_the_notes_a_continue_reads_are_kept_first() -> None:
    notes = outcome_wire(Completed(Delivered(notes=NOTES)))["delivered"]["notes"]
    assert isinstance(notes, list) and len(notes) == 500
    assert notes[:2] == ["notes/handoff.json", "notes/partial-answer.md"]

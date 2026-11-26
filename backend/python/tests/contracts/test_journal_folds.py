"""QA13-15: one journal folds to the same run in Python and in Node.

``contracts/harness/vectors/journal-folds.json`` holds, for every golden journal and every journal of the
run machine's library (approvals denied, crashes, cancels, steering, unknown effects, quarantine), the
summary Python's fold gives: how the run ended, each call with its outcome or denial, each approval and
its decision, the queued inputs, the children. This test keeps the file equal to what this build's fold
says (record with ``UPDATE_GOLDENS=1``); Node's ``tests/contracts/journal-folds.test.ts`` folds the same
journals with its timeline and must say the same.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from harness_core.journal import JournalEvent
from harness_core.kernel.fold import RunState, fold_run, parse_interaction_id
from tests.unit.harness_core.driver.journal_library import LIBRARY, generated_journal
from tests.unit.harness_core.durable.support import RUN, SESSION

if TYPE_CHECKING:
    from pydantic import JsonValue

VECTORS = Path(__file__).resolve().parents[4] / "contracts" / "harness" / "vectors"
TARGET = VECTORS / "journal-folds.json"
CHILD_STATUS = {"answered": "completed", "needs_input": "completed", "failed": "failed", "cancelled": "cancelled"}
ENDED = {"completed", "failed", "cancelled", "quarantined"}


def _first(item: JsonValue) -> str:
    assert isinstance(item, list)
    return str(item[0])


def fold_summary(state: RunState) -> dict[str, JsonValue]:
    end: dict[str, JsonValue] | None = None
    if state.status in ENDED:
        end = {"status": state.status}
        if state.status in ("completed", "failed") and state.outcome is not None:
            end["detail"] = state.outcome
    calls = sorted(state.invocations.values(), key=lambda i: (i.turn, i.index))
    approvals: list[JsonValue] = []
    for interaction_id, record in state.interactions.items():
        parsed = parse_interaction_id(interaction_id)
        if record.kind != "approval" or parsed is None or parsed[0] != "approve":
            continue
        if record.decision is None:
            status = "expired" if state.status in ENDED else "pending"
        else:
            status = record.decision if record.decision in ("approved", "denied") else "closed"
        approvals.append([interaction_id, status])
    calls_view: list[JsonValue] = [[c.turn, c.call_id, c.status, c.denied_rule is not None] for c in calls]
    inputs: list[JsonValue] = []
    inputs.extend(sorted([*state.queued_inputs, *state.consumed_inputs]))
    children: list[JsonValue] = [
        [child.child_run_id, CHILD_STATUS.get(str(child.outcome), "completed") if child.completed else "running"]
        for child in state.children.values()
    ]
    return {
        "end": end,
        "calls": calls_view,
        "approvals": sorted(approvals, key=_first),
        "inputs": inputs,
        "children": sorted(children, key=_first),
    }


def _summary(events: list[JournalEvent], run_id: str) -> dict[str, JsonValue]:
    return fold_summary(fold_run(events, run_id=run_id))


def vector() -> dict[str, JsonValue]:
    journals: list[JsonValue] = []
    for path in sorted(VECTORS.glob("journals/*.journal.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        events = [JournalEvent.from_envelope(raw) for raw in doc["events"]]
        journals.append(
            {
                "name": path.name.removesuffix(".journal.json"),
                "journal": str(path.relative_to(VECTORS)),
                "runId": doc["runId"],
                "summary": _summary(events, doc["runId"]),
            }
        )
    for name in LIBRARY:
        events = generated_journal(name)
        journals.append(
            {
                "name": f"library/{name}",
                "runId": RUN,
                "sessionId": SESSION,
                "events": [e.envelope() for e in events],
                "summary": _summary(events, RUN),
            }
        )
    return {"journals": journals}


def _dump(doc: dict[str, JsonValue]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def test_qa13_15_the_vector_is_what_this_builds_fold_says() -> None:
    actual = _dump(vector())
    if os.environ.get("UPDATE_GOLDENS") == "1":
        TARGET.write_text(actual, encoding="utf-8")
        return
    assert TARGET.exists(), f"missing {TARGET.name}; record it with UPDATE_GOLDENS=1"
    assert actual == TARGET.read_text(encoding="utf-8"), (
        f"{TARGET.name} drifted: the fold reads a journal differently; re-record with UPDATE_GOLDENS=1 only if "
        "intended, then run Node's journal-folds.test.ts"
    )


def test_qa13_15_the_vector_covers_every_way_a_run_ends() -> None:
    doc = json.loads(TARGET.read_text(encoding="utf-8"))
    ends = {str((j["summary"]["end"] or {"status": "open"})["status"]) for j in doc["journals"]}
    assert ends >= {"completed", "failed", "cancelled", "quarantined", "open"}
    decisions = {status for j in doc["journals"] for _, status in j["summary"]["approvals"]}
    assert decisions >= {"approved", "denied", "pending"}

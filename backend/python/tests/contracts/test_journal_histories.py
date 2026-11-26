"""QA13-3 (JRN-22): every journal the repository keeps as a vector is a valid history.

The golden journals (recorded runs, read by Node's projector), every replay corpus's journals and the
L4 journals the fault suite saved from real processes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core.journal import JournalEvent
from harness_core.testing.history import journal_history_violations

VECTORS = Path(__file__).resolve().parents[4] / "contracts" / "harness" / "vectors"
FILES = sorted(
    [
        *VECTORS.glob("journals/*.journal.json"),
        *VECTORS.glob("replay-corpus/*/journals/*.journal.json"),
        *VECTORS.glob("replay-corpus/*/l4/*.journal.json"),
    ]
)
# Written by hand for the U3 screens, not recorded from a run: no pin, an outcome without its intent.
HAND_WRITTEN = {"u3_engine_run", "u3_parent_with_engine_child"}


def _case(path: Path) -> object:
    name = path.name.removesuffix(".journal.json")
    marks = (
        [pytest.mark.xfail(strict=True, reason="QA13-F1: hand-written U3 fixture, not a recorded history")]
        if name in HAND_WRITTEN
        else []
    )
    return pytest.param(path, id=str(path.relative_to(VECTORS)), marks=marks)


def test_qa13_3_there_are_journals_to_check() -> None:
    assert len(FILES) > 40


@pytest.mark.parametrize("path", [_case(p) for p in FILES])
def test_qa13_3_a_kept_journal_is_a_valid_history(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    events = [JournalEvent.from_envelope(raw) for raw in doc["events"]]
    assert [str(v) for v in journal_history_violations(events)] == []

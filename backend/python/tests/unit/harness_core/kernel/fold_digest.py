"""Prints the canonical digest of each journal's folded run and session state (QA13-12's subprocess).

``python -m tests.unit.harness_core.kernel.fold_digest <journal.json>...``: one ``<path> <digest>`` line each.
A journal file holds ``{"runId", "sessionId", "events": [envelope, ...]}``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from harness_core.journal import JournalEvent
from harness_core.kernel.fold import fold_run, fold_session


def fold_digest(doc: dict[str, Any]) -> str:
    events = [JournalEvent.from_envelope(raw) for raw in doc["events"]]
    run = fold_run(events, run_id=doc["runId"])
    session = fold_session([e for e in events if e.stream_id == doc["sessionId"]], stream_id=doc["sessionId"])
    # Not canonical JSON: that sorts keys, and the order the fold built a mapping in is what must not vary.
    state = {"run": run.model_dump(mode="json"), "session": session.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()


def main(paths: list[str]) -> None:
    for path in paths:
        print(path, fold_digest(json.loads(Path(path).read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main(sys.argv[1:])

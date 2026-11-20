"""Every writer of ``hasIndexedCopy`` is accounted for: it writes the parser identity with the copy, or it cannot be a COMPLETED copy.

A writer that sets COMPLETED on a record without the identity makes the record's previous identity stale (the timestamp moves), which
costs one full run; a writer that moves a record to another copy without it would label that copy with a parser it was not built by.
Adding a writer fails here until it is placed in one of the two lists.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"

#: Writers of a COMPLETED copy: each builds the identity fields through ``parser_identity`` (the run's own write, the twin attach, the
#: promotions of QUEUED duplicates on the three stores).
WRITES_THE_IDENTITY = {
    "modules/transformers/sink_orchestrator.py": "indexed_parser_fields",
    "events/events.py": "copied_identity_fields",
    "services/graph_db/arango/arango_http_provider.py": "promotion_plan",
    "services/graph_db/neo4j/neo4j_provider.py": "promotion_plan",
    "services/graph_db/postgres/sync_state.py": "plan_promotions",
}

#: Writers that use ``indexed_copy_fields`` and never make a COMPLETED copy of a tracked parser: EMPTY (no copy), a status the caller
#: names for the code graph (code and SQL are not tracked), and the stage-state status writers of an existing copy.
NEEDS_NONE = {
    "events/processor.py": "EMPTY: the copy is cleared",
    "modules/transformers/pipeline.py": "EMPTY: the copy is cleared",
    "services/graph_db/postgres/codegraph.py": "a status write by revision for the stage runtime: the copy and its timestamp are not changed",
}


def _files_using_indexed_copy_fields() -> set[str]:
    found = set()
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?<!def )\bindexed_copy_fields\(", text):
            found.add(path.relative_to(APP).as_posix())
    return found


def test_every_writer_of_has_indexed_copy_is_listed() -> None:
    listed = set(WRITES_THE_IDENTITY) | {k for k in NEEDS_NONE if "#" not in k}
    extra = _files_using_indexed_copy_fields() - listed - {
        "services/graph_db/arango/arango_http_provider.py", "services/graph_db/neo4j/neo4j_provider.py",
        "services/graph_db/common/record_visibility.py",
    }
    assert not extra, f"a writer of hasIndexedCopy that writes no parser identity and is not accounted for: {sorted(extra)}"


def test_the_writers_that_make_a_completed_copy_use_the_identity_builders() -> None:
    for relative, builder in WRITES_THE_IDENTITY.items():
        text = (APP / relative).read_text(encoding="utf-8")
        assert builder in text and "parser_identity" in text, f"{relative} writes COMPLETED but not through {builder}"

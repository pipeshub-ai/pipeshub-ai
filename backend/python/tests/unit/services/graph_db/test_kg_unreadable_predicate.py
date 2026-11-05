"""The five statements that decide whether a change-log row can be read (the drain's two reads, the lag, and the two counts of the unreadable ones) carry one and the same predicate:
a row is in the readable set or in the unreadable one, never in neither and never in both."""
from __future__ import annotations

import re
from pathlib import Path

from app.services.graph_db.postgres.kg import drainer

PREDICATE = re.compile(r"e IS NULL OR e !~ '[^']*' OR length\(e\) > \d+")


def test_every_statement_that_judges_a_change_log_row_uses_the_same_predicate() -> None:
    found = PREDICATE.findall(Path(drainer.__file__).read_text(encoding="utf-8"))
    assert len(found) >= 5 and len(set(found)) == 1, found

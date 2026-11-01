"""What ``get_record_query_candidates`` returns; see ``docs/record-query.md``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RecordCandidates:
    """One window of candidate rows in order. ``window_size`` below the
    window asked for means the candidates ended; ``capped`` means the scan
    stopped at its cap, so the end of paging is not the end of the matches."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    window_size: int = 0
    capped: bool = False

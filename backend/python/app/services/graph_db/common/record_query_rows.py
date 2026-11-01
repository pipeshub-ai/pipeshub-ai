"""What ``get_record_query_candidates`` and ``get_record_query_rows`` return,
and what both providers share to build them; see ``docs/record-query.md``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from app.modules.retrieval.record_fields import ORGANIZATION_EDGES, PERSON_EDGES

if TYPE_CHECKING:
    from app.modules.retrieval.record_query.pushdown import Pushdown

# People, organisations and links read per row: hydration runs once per
# window row, so a record linked to thousands of others stays one bounded read.
LINKED_PER_ROW = 100

__all__ = [
    "LINKED_PER_ROW", "ORGANIZATION_EDGES", "PERSON_EDGES", "CandidateKey", "RecordCandidates", "matches_nothing",
    "record_query_seed",
]


class CandidateKey(NamedTuple):
    """A candidate record and its place in the candidate order."""

    id: str
    sort_ts: int


@dataclass(frozen=True)
class RecordCandidates:
    """Every candidate the scan read, newest first then id; ``capped`` means
    the scan stopped at its cap, so these are not all the matches."""

    keys: list[CandidateKey] = field(default_factory=list)
    capped: bool = False


def record_query_seed(pushed: Pushdown) -> str:
    """Which condition the scan starts from: the first edge condition present,
    then containers, else every record of the org. Starting from a person's
    or an entity's edges reads only their records instead of the tenant's;
    every condition is still checked on each candidate."""
    for key in ("entities", "links", "people", "organizations", "containers"):
        if pushed.get(key):
            return key
    return "org"


def matches_nothing(pushed: Pushdown) -> bool:
    """Whether a key holds an empty choice, which no record meets: two
    conditions on one field that share no value, or a condition naming no one."""
    if any(k in pushed and not pushed[k] for k in ("sources", "record_types", "containers")):
        return True
    return (
        any(not p["ids"] or not p["edges"] for p in pushed.get("people", []))
        or any(not ids for ids in pushed.get("organizations", []))
        or any(not link["ids"] for link in pushed.get("links", []))
    )

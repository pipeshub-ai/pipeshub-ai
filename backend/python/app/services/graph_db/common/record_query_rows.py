"""What ``get_record_query_candidates`` and ``get_record_query_rows`` return,
and what both providers share to build them; see ``docs/record-query.md``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from app.config.constants.arangodb import ProgressStatus
from app.modules.retrieval.record_fields import ORGANIZATION_EDGES, PERSON_EDGES
from app.services.graph_db.taxonomy import TAXONOMY_ENTITY_TYPES

if TYPE_CHECKING:
    from app.modules.retrieval.record_query.pushdown import Pushdown

# People, organisations and links read per row: hydration runs once per
# window row, so a record linked to thousands of others stays one bounded read.
LINKED_PER_ROW = 100

# Records reach these only over edges classification writes (and organisations
# also over the MENTIONS edges it writes), so a record not classified yet lacks
# them whatever it is about. See "Classify on first use" in docs/indexing-service.md.
CLASSIFIED_ENTITY_TYPES: frozenset[str] = TAXONOMY_ENTITY_TYPES | {"organization"}
# extractionStatus of a record whose classification has not run yet; a record
# with none stored reads the same.
CLASSIFICATION_PENDING: tuple[str, ...] = (
    ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value, ProgressStatus.PAUSED.value,
)
# Classification ran and failed: the taxonomy is not known either, but asking
# again on first use would retry it forever; a reindex retries it.
CLASSIFICATION_FAILED: tuple[str, ...] = (ProgressStatus.FAILED.value,)
# Every status whose record may lack the edges classification writes. SKIPPED
# is not here: classification does not apply to that record, so it has none.
CLASSIFICATION_UNDECIDED: tuple[str, ...] = CLASSIFICATION_PENDING + CLASSIFICATION_FAILED

__all__ = [
    "CLASSIFICATION_FAILED", "CLASSIFICATION_PENDING", "CLASSIFICATION_UNDECIDED", "CLASSIFIED_ENTITY_TYPES", "LINKED_PER_ROW", "ORGANIZATION_EDGES", "PERSON_EDGES",
    "CandidateKey", "RecordCandidates", "Seed", "classification_failed", "classification_pending", "classified_entities", "matches_nothing",
    "record_query_seed",
]


def classification_pending(row: dict[str, Any]) -> bool:
    """Whether the row's record has not been classified yet."""
    status = row.get("extractionStatus")
    return status is None or status in CLASSIFICATION_PENDING


def classification_failed(row: dict[str, Any]) -> bool:
    return row.get("extractionStatus") in CLASSIFICATION_FAILED


def classified_entities(pushed: Pushdown) -> list[dict[str, str]]:
    """The pushed entity conditions only classification links a record to."""
    return [e for e in pushed.get("entities", []) if e.get("type") in CLASSIFIED_ENTITY_TYPES]


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


@dataclass(frozen=True)
class Seed:
    """Where the scan starts: ``key`` names the condition, ``entry`` is the
    pushed entry seeded from, and ``with_pending`` adds every record not
    classified yet, which the entry's edges cannot reach."""

    key: str
    entry: Any = None
    with_pending: bool = False


def record_query_seed(pushed: Pushdown) -> Seed:
    """Which condition the scan starts from. Starting from an entity's, a
    link's or a person's edges reads only their records instead of the
    tenant's; every condition is still checked on each candidate. A condition
    classification satisfies must also read every record not classified yet,
    so a container, which needs no such addition, is preferred to it."""
    exact = [e for e in pushed.get("entities", []) if e.get("type") not in CLASSIFIED_ENTITY_TYPES]
    if exact:
        return Seed("entities", exact[0])
    for key in ("links", "people"):
        if pushed.get(key):
            return Seed(key, pushed[key][0])
    if pushed.get("containers"):
        return Seed("containers")
    if pushed.get("entities"):
        return Seed("entities", pushed["entities"][0], with_pending=True)
    if pushed.get("organizations"):
        return Seed("organizations", pushed["organizations"][0], with_pending=True)
    return Seed("org")


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

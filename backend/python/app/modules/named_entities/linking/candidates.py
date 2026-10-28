"""Stored entities a mention may name, kept only when the user can read a record
that mentions them.

Four sources, cheapest and most precise first:

- exact: the node ids indexing would have written for the name under each
  candidate kind (``keys.named_entity_key``), read by primary key;
- prefix: for people, organizations and products, nodes whose key is the name
  followed by more words ("Sam" -> "Sam Lee"), which also reaches people, whose
  names are never embedded;
- fuzzy: for a person's full name, people with the same first word whose rest
  is a near spelling ("Maya Castelanos" -> "Maya Castellanos"), read in the same
  call as the prefixes;
- dense: the entity vector search, already permission-checked by
  ``search_entities_for_user``.

Every exact or prefix candidate passes ``probe_permitted_entities`` before
anything is decided about it, so whether a node exists that the user cannot
read changes nothing a caller sees.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.modules.named_entities.domain.kinds import VECTOR_KINDS, EntityKind
from app.modules.named_entities.keys import named_entity_key
from app.modules.retrieval.entity_permissions import (
    EntityHit,
    EntitySearchTimedOut,
    probe_permitted_entities,
)

if TYPE_CHECKING:
    from app.modules.named_entities.linking.mentions import Mention
    from app.modules.retrieval.entity_permissions import (
        EntityAccessContext,
        EntityProbe,
    )
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

NAMED_ENTITY = "named_entity"
MAX_CANDIDATES = 5
PREFIX_ROW_LIMIT = 21
PREFIX_MIN_CHARS = 3
PREFIX_KINDS = frozenset({EntityKind.PERSON, EntityKind.ORGANIZATION, EntityKind.PRODUCT})
# A near spelling of a person's name after the first word: one or two letters
# off in a surname ("castelanos"), not a different short one ("berg"/"borg").
FUZZY_MIN_TAIL_CHARS = 4
FUZZY_MIN_RATIO = 0.85

DenseSearch = Callable[[str, list[str]], Awaitable[list[EntityHit]]]
"""``(name, kinds) -> permission-checked hits`` (``search_entities_for_user``)."""
Probe = Callable[["IGraphDBProvider", "EntityAccessContext", list[tuple[str, str]]], Awaitable["EntityProbe"]]


class CandidateSource(str, Enum):
    EXACT = "exact"
    PREFIX = "prefix"
    FUZZY = "fuzzy"
    DENSE = "dense"


_RANK = {CandidateSource.EXACT: 0, CandidateSource.PREFIX: 1, CandidateSource.FUZZY: 2, CandidateSource.DENSE: 3}


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    kind: str
    name: str
    source: CandidateSource
    records: tuple[dict[str, Any], ...]
    """Readable records that mention it, newest first (a preview)."""
    more_records: bool


@dataclass
class MentionCandidates:
    mention: Mention
    candidates: list[Candidate] = field(default_factory=list)
    ambiguous: bool = False
    """More readable prefix matches than can be told apart; ask for the full name."""


@dataclass
class CandidateSet:
    per_mention: list[MentionCandidates]
    timed_out: bool
    """An access check or the vector search stopped at its deadline."""


class CandidateFinder:
    def __init__(
        self,
        graph: IGraphDBProvider,
        context: EntityAccessContext,
        *,
        dense: DenseSearch | None,
        probe: Probe = probe_permitted_entities,
    ) -> None:
        self._graph = graph
        self._context = context
        self._dense = dense
        self._probe = probe

    async def find(self, mentions: list[Mention]) -> CandidateSet:
        org_id = self._context.org_id
        exact_ids: dict[str, set[int]] = {}
        for index, mention in enumerate(mentions):
            for kind, key in mention.keys:
                exact_ids.setdefault(named_entity_key(org_id, kind.value, key), set()).add(index)
        prefixes: dict[tuple[str, str], set[int]] = {}
        fuzzy: dict[tuple[str, str], set[int]] = {}
        for index, mention in enumerate(mentions):
            for kind, key in mention.keys:
                if kind in PREFIX_KINDS and len(key.rsplit(":", 1)[-1]) >= PREFIX_MIN_CHARS:
                    prefixes.setdefault((kind.value, key), set()).add(index)
                if (first := _fuzzy_first_word_key(kind, key)) is not None:
                    fuzzy.setdefault((kind.value, first), set()).add(index)

        by_ids, by_prefix, dense = await asyncio.gather(
            self._graph.get_named_entities_by_ids(org_id, sorted(exact_ids)),
            self._graph.find_named_entities_by_key_prefix(org_id, sorted({*prefixes, *fuzzy}), PREFIX_ROW_LIMIT),
            asyncio.gather(*(self._dense_for(mention) for mention in mentions)),
        )
        extended = [row for row in by_prefix if (str(row.get("kind")), str(row.get("key"))) in prefixes]
        near: list[list[dict[str, Any]]] = [[] for _ in mentions]
        for row in by_prefix:
            for index in fuzzy.get((str(row.get("kind")), str(row.get("key"))), ()):
                if index not in exact_ids.get(str(row.get("id")), ()) and _near_spelling(mentions[index], row):
                    near[index].append(row)
        # Only rows that can become candidates are access-checked.
        rows = {row["id"]: row for row in [*by_ids, *extended, *(r for hits in near for r in hits)] if row.get("id")}
        probe = await self._probe(self._graph, self._context, [(entity_id, NAMED_ENTITY) for entity_id in rows])
        timed_out = probe.timed_out or any(late for _, late in dense)

        per_mention = [MentionCandidates(mention=mention) for mention in mentions]
        found: list[dict[str, Candidate]] = [{} for _ in mentions]

        def _add(index: int, row: dict[str, Any], source: CandidateSource) -> None:
            permitted = probe.permitted.get(row["id"])
            if permitted is None:
                return
            current = found[index].get(row["id"])
            if current is None or _RANK[source] < _RANK[current.source]:
                found[index][row["id"]] = Candidate(
                    entity_id=row["id"], kind=str(row.get("kind") or ""), name=str(row.get("name") or ""),
                    source=source, records=tuple(permitted.records), more_records=permitted.more_records,
                )

        for row in by_ids:
            for index in exact_ids.get(row.get("id", ""), ()):
                _add(index, row, CandidateSource.EXACT)
        prefix_hits: list[list[dict[str, Any]]] = [[] for _ in mentions]
        for row in extended:
            if row.get("id") not in probe.permitted:
                continue
            for index in prefixes.get((str(row.get("kind")), str(row.get("key"))), ()):
                prefix_hits[index].append(row)
        for index, hits in enumerate(prefix_hits):
            if len(hits) > MAX_CANDIDATES:
                per_mention[index].ambiguous = True
                continue
            for row in sorted(hits, key=lambda r: str(r.get("normKey"))):
                _add(index, row, CandidateSource.PREFIX)
        for index, hits in enumerate(near):
            for row in sorted(hits, key=lambda r: str(r.get("normKey"))):
                _add(index, row, CandidateSource.FUZZY)
        for index, (hits, _) in enumerate(dense):
            for hit in hits:
                if hit.entity_type == NAMED_ENTITY and hit.entity_id not in found[index]:
                    found[index][hit.entity_id] = Candidate(
                        entity_id=hit.entity_id, kind=hit.kind, name=hit.name, source=CandidateSource.DENSE,
                        records=tuple(hit.records), more_records=hit.more_records,
                    )

        for index, entry in enumerate(per_mention):
            ordered = sorted(found[index].values(), key=lambda c: _RANK[c.source])
            # Exact nodes are kept outside the cap: they are taken, not offered,
            # so they must not push a longer name out of the check.
            exact = [c for c in ordered if c.source is CandidateSource.EXACT]
            others = [c for c in ordered if c.source is not CandidateSource.EXACT]
            entry.candidates = [*exact, *others[:MAX_CANDIDATES]]
        return CandidateSet(per_mention=per_mention, timed_out=timed_out)

    async def _dense_for(self, mention: Mention) -> tuple[list[EntityHit], bool]:
        kinds = [kind.value for kind in mention.kinds if kind in VECTOR_KINDS]
        if self._dense is None or not kinds:
            return [], False
        try:
            return await self._dense(mention.name, kinds), False
        except EntitySearchTimedOut:
            return [], True


def _fuzzy_first_word_key(kind: EntityKind, key: str) -> str | None:
    """The key of a person's first word ("name:person:maya" for "name:person:maya
    castelanos"), whose prefix rows hold the near spellings; ``None`` for a
    one-word name or a short rest, where a near spelling is a different name.

    shortcut: a typo in the first word is not found, and only the first
    ``PREFIX_ROW_LIMIT`` people sharing that word are compared; index a phonetic
    or trigram key if misspelled first names or very common ones matter."""
    if kind is not EntityKind.PERSON:
        return None
    head, _, name = key.rpartition(":")
    first, _, rest = name.partition(" ")
    # A short first word is an initial or a title ("j.", "dr."), not the name to compare after.
    if len(first) < PREFIX_MIN_CHARS or not rest or len(rest.replace(" ", "")) < FUZZY_MIN_TAIL_CHARS:
        return None
    return f"{head}:{first}"


def _near_spelling(mention: Mention, row: dict[str, Any]) -> bool:
    """Whether a stored person is the mention's name with a near spelling after
    the same first word (same number of words, not identical)."""
    stored = str(row.get("normKey") or "").rpartition(":")[2]
    for kind, key in mention.keys:
        if kind is not EntityKind.PERSON:
            continue
        asked = key.rpartition(":")[2]
        if asked == stored or len(asked.split()) != len(stored.split()):
            continue
        asked_rest, stored_rest = asked.partition(" ")[2], stored.partition(" ")[2]
        if SequenceMatcher(None, asked_rest, stored_rest).ratio() >= FUZZY_MIN_RATIO:
            return True
    return False

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
  ``search_entities_for_user``;
- alias: for organizations and products, the nodes a name is the other half of
  a learned short form for: the asked name carried as an alias by exactly one
  node ("Pinecrest" -> "Pinecrest Hospitality"), and an exact node's own aliases
  stored as nodes of their own ("Pinecrest Hospitality" -> "Pinecrest"), each only
  while no other node carries that alias. Taken like an exact match.

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

from app.modules.named_entities.domain.kinds import (
    ALIAS_KINDS,
    VECTOR_KINDS,
    EntityKind,
)
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
    ALIAS = "alias"
    PREFIX = "prefix"
    FUZZY = "fuzzy"
    DENSE = "dense"


_RANK = {
    CandidateSource.EXACT: 0, CandidateSource.ALIAS: 1, CandidateSource.PREFIX: 2,
    CandidateSource.FUZZY: 3, CandidateSource.DENSE: 4,
}
# Sources taken without the verifier's check: the name's own node, or one a learned
# alias ties to it.
SETTLED_SOURCES = frozenset({CandidateSource.EXACT, CandidateSource.ALIAS})
# Carriers read per alias: two are enough to know it is not unique.
_ALIAS_ROW_LIMIT = 2


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
    more_related: bool = False
    """Longer names under a related kind were cut by the candidate cap, so the
    check never saw them; asking for that kind reaches them."""


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
        aliases: bool = True,
    ) -> None:
        self._graph = graph
        self._context = context
        self._dense = dense
        self._probe = probe
        self._aliases = aliases

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

        asked_aliases: dict[tuple[str, str], set[int]] = {}
        if self._aliases:
            for index, mention in enumerate(mentions):
                for kind, key in mention.keys:
                    if kind in ALIAS_KINDS:
                        asked_aliases.setdefault((kind.value, key), set()).add(index)
        by_ids, by_prefix, dense, carriers = await asyncio.gather(
            self._graph.get_named_entities_by_ids(org_id, sorted(exact_ids)),
            self._graph.find_named_entities_by_key_prefix(org_id, sorted({*prefixes, *fuzzy}), PREFIX_ROW_LIMIT),
            asyncio.gather(*(self._dense_for(mention) for mention in mentions)),
            self._carriers(org_id, sorted(asked_aliases)),
        )
        # (node, the exact node it was reached from or None): an alias reached through a
        # node the user cannot read must not show that node exists.
        family: list[list[tuple[dict[str, Any], str | None]]] = [[] for _ in mentions]
        # Asked name -> the one node carrying it as an alias.
        for pair, rows in carriers.items():
            if len(rows) == 1:
                for index in asked_aliases[pair]:
                    family[index].append((rows[0], None))
        # Exact node -> its own aliases that are nodes of their own.
        for index, rows in enumerate(await self._own_aliases(org_id, by_ids, exact_ids, len(mentions))):
            family[index].extend(rows)
        extended = [row for row in by_prefix if (str(row.get("kind")), str(row.get("key"))) in prefixes]
        near: list[list[dict[str, Any]]] = [[] for _ in mentions]
        for row in by_prefix:
            for index in fuzzy.get((str(row.get("kind")), str(row.get("key"))), ()):
                if index not in exact_ids.get(str(row.get("id")), ()) and _near_spelling(mentions[index], row):
                    near[index].append(row)
        # Only rows that can become candidates are access-checked.
        rows = {
            row["id"]: row
            for row in [*by_ids, *extended, *(r for hits in near for r in hits), *(r for hits in family for r, _ in hits)]
            if row.get("id")
        }
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
        for index, hits in enumerate(family):
            for row, owner in hits:
                if owner is None or owner in probe.permitted:
                    _add(index, row, CandidateSource.ALIAS)
        prefix_hits: list[list[dict[str, Any]]] = [[] for _ in mentions]
        for row in extended:
            if row.get("id") not in probe.permitted:
                continue
            for index in prefixes.get((str(row.get("kind")), str(row.get("key"))), ()):
                prefix_hits[index].append(row)
        for index, hits in enumerate(prefix_hits):
            # Only the asked kind can make a name ambiguous; rows of a related kind
            # come after it, so the candidate cap below drops them first.
            asked = [r for r in hits if mentions[index].settles(str(r.get("kind")))]
            if len(asked) > MAX_CANDIDATES:
                per_mention[index].ambiguous = True
                continue
            related = [r for r in hits if not mentions[index].settles(str(r.get("kind")))]
            for row in [*sorted(asked, key=_norm_key), *sorted(related, key=_norm_key)]:
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
            exact = [c for c in ordered if c.source in SETTLED_SOURCES]
            others = [c for c in ordered if c.source not in SETTLED_SOURCES]
            entry.candidates = [*exact, *others[:MAX_CANDIDATES]]
            entry.more_related = any(
                c.source is CandidateSource.PREFIX and not entry.mention.settles(c.kind)
                for c in others[MAX_CANDIDATES:]
            )
        return CandidateSet(per_mention=per_mention, timed_out=timed_out)

    async def _carriers(self, org_id: str, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Each ``(kind, key)`` to the nodes carrying it as an alias that is not their own key."""
        rows = await self._graph.find_named_entities_by_alias(org_id, pairs, _ALIAS_ROW_LIMIT) if pairs else []
        found: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows or []:
            if row.get("id"):
                found.setdefault((str(row.get("kind")), str(row.get("key"))), []).append(row)
        return found

    async def _own_aliases(
        self, org_id: str, exact_rows: list[dict[str, Any]], exact_ids: dict[str, set[int]], count: int,
    ) -> list[list[tuple[dict[str, Any], str]]]:
        """Per mention, the nodes of its exact nodes' learned aliases ("Pinecrest" for
        "Pinecrest Hospitality"), each only when that node alone carries the alias, with
        the exact node it came from."""
        per_mention: list[list[tuple[dict[str, Any], str]]] = [[] for _ in range(count)]
        if not self._aliases:
            return per_mention
        kinds = {kind.value for kind in ALIAS_KINDS}
        wanted: dict[str, tuple[str, str, str, set[int]]] = {}
        for row in exact_rows:
            kind, own = str(row.get("kind")), row.get("normKey")
            if kind not in kinds or not row.get("id"):
                continue
            for alias in row.get("normalizedAliases") or []:
                if alias and alias != own:
                    wanted[named_entity_key(org_id, kind, alias)] = (kind, alias, row["id"], exact_ids.get(row["id"], set()))
        if not wanted:
            return per_mention
        nodes, carriers = await asyncio.gather(
            self._graph.get_named_entities_by_ids(org_id, sorted(wanted)),
            self._carriers(org_id, sorted({(kind, alias) for kind, alias, _, _ in wanted.values()})),
        )
        for node in nodes or []:
            kind, alias, owner, indexes = wanted.get(node.get("id", ""), ("", "", "", set()))
            if [row["id"] for row in carriers.get((kind, alias), [])] == [owner]:
                for index in indexes:
                    per_mention[index].append((node, owner))
        return per_mention

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


def _norm_key(row: dict[str, Any]) -> str:
    return str(row.get("normKey"))


async def alias_family(
    graph: IGraphDBProvider,
    context: EntityAccessContext,
    entity_id: str,
    *,
    probe: Probe = probe_permitted_entities,
) -> list[dict[str, Any]]:
    """The readable nodes a learned alias ties to ``entity_id``: its own aliases stored
    as nodes ("Pinecrest" for "Pinecrest Hospitality") and the node carrying its key as
    an alias, each only while no other node carries that alias. ``{id, kind, name}``."""
    org_id = context.org_id
    nodes = await graph.get_named_entities_by_ids(org_id, [entity_id])
    node = nodes[0] if nodes else None
    kinds = {kind.value for kind in ALIAS_KINDS}
    if node is None or str(node.get("kind")) not in kinds:
        return []
    kind, own = str(node["kind"]), node.get("normKey")
    aliases = [alias for alias in node.get("normalizedAliases") or [] if alias and alias != own]
    by_id = {named_entity_key(org_id, kind, alias): alias for alias in aliases}
    pairs = sorted({(kind, alias) for alias in aliases} | ({(kind, own)} if own else set()))
    short_nodes, rows = await asyncio.gather(
        graph.get_named_entities_by_ids(org_id, sorted(by_id)) if by_id else _no_rows(),
        graph.find_named_entities_by_alias(org_id, pairs, _ALIAS_ROW_LIMIT) if pairs else _no_rows(),
    )
    carriers: dict[str, list[str]] = {}
    for row in rows or []:
        carriers.setdefault(str(row.get("key")), []).append(str(row.get("id")))
    family = [n for n in short_nodes or [] if carriers.get(by_id.get(n.get("id", ""), "")) == [entity_id]]
    reverse = carriers.get(str(own), [])
    if len(reverse) == 1 and reverse[0] != entity_id:
        family += list(await graph.get_named_entities_by_ids(org_id, reverse) or [])
    family = list({n["id"]: n for n in family if n.get("id")}.values())
    if not family:
        return []
    # The node asked about must be readable too, or its aliases would show it exists.
    probed = [(entity_id, NAMED_ENTITY), *((n["id"], NAMED_ENTITY) for n in family)]
    permitted = (await probe(graph, context, probed)).permitted
    if entity_id not in permitted:
        return []
    return [{"id": n["id"], "kind": n.get("kind"), "name": n.get("name")} for n in family if n["id"] in permitted]


async def _no_rows() -> list[dict[str, Any]]:
    return []

"""From mentions to the entities they name: candidates, then one check for
the names no exact match settles.

A name is settled when a readable node carries exactly the key indexing gives
it under the kind asked for (any kind, when none was asked). Every other name
with candidates goes to the verifier in one call. Names are shown from the
records the user can read (the spellings on their mention edges), never from
whichever record first created the node.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from app.modules.named_entities.linking.candidates import (
    Candidate,
    CandidateFinder,
    CandidateSource,
)
from app.modules.named_entities.linking.verifier import (
    MentionVerifier,
    OfferedCandidate,
    VerifyItem,
    offerable,
)

if TYPE_CHECKING:
    from app.modules.named_entities.linking.mentions import Mention
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class LinkStatus(str, Enum):
    MATCHED = "matched"
    """A readable node has exactly the name's key."""
    CHECKED = "checked"
    """The verifier chose the entities."""
    UNVERIFIED = "unverified"
    """Candidates exist but could not be checked; none was used."""
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    """Too many readable entities start with the name to tell apart."""


@dataclass(frozen=True)
class LinkResult:
    mention: Mention
    status: LinkStatus
    accepted: list[Candidate] = field(default_factory=list)
    rejected: list[Candidate] = field(default_factory=list)
    """Readable look-alikes that are not the same thing."""
    unchecked: list[Candidate] = field(default_factory=list)
    """Possible matches the verifier could not check (``UNVERIFIED``)."""


@dataclass(frozen=True)
class Linking:
    results: list[LinkResult]
    timed_out: bool
    verifier_called: bool
    names: dict[str, str]
    """Entity id to the name shown for it."""


async def readable_names(graph: IGraphDBProvider, org_id: str, candidates: list[Candidate]) -> dict[str, str]:
    """Each candidate's most frequent spelling on the mention edges of readable
    records it was found through; its stored name when none records one."""
    names = {candidate.entity_id: candidate.name for candidate in candidates}
    record_ids = sorted({str(row["_key"]) for c in candidates for row in c.records if row.get("_key")})
    if not record_ids:
        return names
    rows = await graph.get_named_entity_mentions_in_records(org_id, sorted(names), record_ids)
    spellings: dict[str, Counter[str]] = {}
    for row in rows:
        for spelling in row.get("extractedNames") or []:
            if isinstance(spelling, str) and spelling.strip():
                spellings.setdefault(str(row.get("entityId")), Counter())[spelling.strip()] += 1
    for entity_id, counter in spellings.items():
        if entity_id in names:
            names[entity_id] = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return names


class EntityLinker:
    def __init__(
        self,
        finder: CandidateFinder,
        verifier: MentionVerifier | None,
        graph: IGraphDBProvider,
        org_id: str,
    ) -> None:
        self._finder = finder
        self._verifier = verifier
        self._graph = graph
        self._org_id = org_id

    async def link(self, mentions: list[Mention], question: str) -> Linking:
        found = await self._finder.find(mentions)
        every = [c for entry in found.per_mention for c in entry.candidates]
        names = await readable_names(self._graph, self._org_id, every) if every else {}

        results: list[LinkResult | None] = []
        to_check: list[tuple[int, Mention, list[Candidate], list[Candidate]]] = []
        for entry in found.per_mention:
            mention = entry.mention
            exact = [
                c for c in entry.candidates
                if c.source is CandidateSource.EXACT and mention.hinted(c.kind)
            ]
            if exact:
                results.append(LinkResult(mention, LinkStatus.MATCHED, accepted=exact))
            elif entry.ambiguous:
                results.append(LinkResult(mention, LinkStatus.AMBIGUOUS))
            elif not entry.candidates:
                results.append(LinkResult(mention, LinkStatus.NOT_FOUND))
            else:
                offered = [c for c in entry.candidates if offerable(mention.name, names.get(c.entity_id, c.name))]
                refused = [c for c in entry.candidates if c not in offered]
                if not offered:
                    results.append(LinkResult(mention, LinkStatus.NOT_FOUND, rejected=refused))
                else:
                    to_check.append((len(results), mention, offered, refused))
                    results.append(None)

        decided: dict[int, list[int]] | None = {}
        if to_check:
            items = [
                VerifyItem(
                    mention=mention.name,
                    kind_hint=mention.kind_hint.value if mention.kind_hint else None,
                    candidates=[
                        OfferedCandidate(names.get(c.entity_id, c.name), c.kind or "entity", _records(c))
                        for c in offered
                    ],
                )
                for _, mention, offered, _ in to_check
            ]
            decided = await self._verifier.verify(question, items) if self._verifier else None
        for number, (position, mention, offered, refused) in enumerate(to_check):
            chosen = None if decided is None else decided.get(number)
            if chosen is None:
                results[position] = LinkResult(mention, LinkStatus.UNVERIFIED, rejected=refused, unchecked=offered)
                continue
            accepted = [offered[index] for index in chosen]
            rejected = [*(c for c in offered if c not in accepted), *refused]
            status = LinkStatus.CHECKED if accepted else LinkStatus.NOT_FOUND
            results[position] = LinkResult(mention, status, accepted=accepted, rejected=rejected)

        return Linking(
            results=[result for result in results if result is not None],
            timed_out=found.timed_out,
            verifier_called=bool(to_check) and self._verifier is not None,
            names=names,
        )


def _records(candidate: Candidate) -> str:
    count = len(candidate.records)
    if candidate.more_records:
        return f"{count}+ records"
    return f"{count} record" if count == 1 else f"{count} records"

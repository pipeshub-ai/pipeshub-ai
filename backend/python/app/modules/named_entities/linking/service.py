"""From mentions to the entities they name: candidates, then one check for
the names no exact match settles.

A readable node carrying exactly the key indexing gives the name, under the
kind asked for (any kind, when none was asked), is always taken. Every other
name with candidates, and an exact one that also starts longer readable names
("Sam" beside "Sam Lee"), goes to the verifier in one call; the check can add
to an exact match but never drop it. Names are shown from the
records the user can read (the spellings on their mention edges), never from
whichever record first created the node.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING

from app.modules.named_entities.domain.kinds import EntityKind
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
from app.modules.retrieval.entity_permissions import readable_spellings

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
    """Too many readable entities start with the name to tell apart, or it is a
    first name several readable people share (``namesakes``)."""


@dataclass(frozen=True)
class LinkResult:
    mention: Mention
    status: LinkStatus
    accepted: list[Candidate] = field(default_factory=list)
    rejected: list[Candidate] = field(default_factory=list)
    """Readable look-alikes that are not the same thing."""
    unchecked: list[Candidate] = field(default_factory=list)
    """Possible matches the verifier could not check: every candidate of an
    ``UNVERIFIED`` name, or the longer names beside an exact match."""
    more_longer_names: bool = False
    """Beside an exact match, more readable names start with it than one check
    can tell apart, so none of them was considered."""
    left_out: list[Candidate] = field(default_factory=list)
    """Longer names beside an exact match that the check did not include; not
    known to be someone else, unlike ``rejected``."""
    namesakes: list[Candidate] = field(default_factory=list)
    """Readable people whose full name starts with a first name asked on its own:
    each may be the one meant, so none is chosen and none is "not the same"."""


@dataclass(frozen=True)
class Linking:
    results: list[LinkResult]
    timed_out: bool
    verifier_called: bool
    names: dict[str, str]
    """Entity id to the name shown for it."""


async def readable_names(graph: IGraphDBProvider, org_id: str, candidates: list[Candidate]) -> dict[str, str]:
    """Each candidate's most frequent spelling on the mention edges of readable
    records it was found through, else its stored name.

    shortcut: the stored-name fallback is reached only when none of those edges
    records a spelling (every writer sets one), so ``search_by_entities`` keeps it
    rather than dropping the candidate; drop it if such edges ever appear."""
    names = {candidate.entity_id: candidate.name for candidate in candidates}
    record_ids = sorted({str(row["_key"]) for c in candidates for row in c.records if row.get("_key")})
    names.update(await readable_spellings(graph, org_id, sorted(names), record_ids))
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
        # (position, mention, offered, refused, settled): ``settled`` is an exact
        # match kept whatever the check says; the check only adds to it.
        to_check: list[tuple[int, Mention, list[Candidate], list[Candidate], list[Candidate]]] = []
        namesakes: dict[int, list[Candidate]] = {}
        for entry in found.per_mention:
            mention = entry.mention
            shared = _namesakes(mention, entry.candidates, names)
            if shared:
                namesakes[len(results)] = shared
            pool = [c for c in entry.candidates if c not in shared]
            exact = [
                c for c in pool
                if c.source is CandidateSource.EXACT and mention.hinted(c.kind)
            ]
            if exact:
                # A first name stored on its own ("Sam") must not hide the full
                # names it starts ("Sam Lee"): those go to the check.
                longer = [
                    c for c in pool
                    if c.source is CandidateSource.PREFIX and mention.hinted(c.kind) and c not in exact
                ]
                offered = [c for c in longer if offerable(mention.name, names.get(c.entity_id, c.name))]
                if offered:
                    to_check.append((len(results), mention, offered, [c for c in longer if c not in offered], exact))
                    results.append(None)
                else:
                    results.append(LinkResult(
                        mention, LinkStatus.MATCHED, accepted=exact, more_longer_names=entry.ambiguous,
                    ))
            elif entry.ambiguous:
                results.append(LinkResult(mention, LinkStatus.AMBIGUOUS))
            elif not pool:
                results.append(LinkResult(mention, LinkStatus.NOT_FOUND))
            else:
                offered = [c for c in pool if offerable(mention.name, names.get(c.entity_id, c.name))]
                refused = [c for c in pool if c not in offered]
                if not offered:
                    results.append(LinkResult(mention, LinkStatus.NOT_FOUND, rejected=refused))
                else:
                    to_check.append((len(results), mention, offered, refused, []))
                    results.append(None)

        decided: dict[int, list[int]] | None = {}
        if to_check:
            items = [
                VerifyItem(
                    mention=mention.name,
                    kind_hint=mention.kind_hint.value if mention.kind_hint else None,
                    candidates=[
                        OfferedCandidate(names.get(c.entity_id, c.name), c.kind or "entity", record_count_label(c))
                        for c in offered
                    ],
                )
                for _, mention, offered, _, _ in to_check
            ]
            decided = await self._verifier.verify(question, items) if self._verifier else None
        for number, (position, mention, offered, refused, settled) in enumerate(to_check):
            chosen = None if decided is None else decided.get(number)
            if chosen is None:
                results[position] = (
                    LinkResult(mention, LinkStatus.MATCHED, accepted=settled, rejected=refused, unchecked=offered)
                    if settled
                    else LinkResult(mention, LinkStatus.UNVERIFIED, rejected=refused, unchecked=offered)
                )
                continue
            picked = [offered[index] for index in chosen]
            passed_over = [c for c in offered if c not in picked]
            # Beside an exact match the check only adds; a longer name it left out may still be the same.
            left_out = [c for c in passed_over if settled and c.source is CandidateSource.PREFIX]
            rejected = [*(c for c in passed_over if c not in left_out), *refused]
            if settled:
                status = LinkStatus.CHECKED if picked else LinkStatus.MATCHED
            else:
                status = LinkStatus.CHECKED if picked else LinkStatus.NOT_FOUND
            results[position] = LinkResult(
                mention, status, accepted=[*settled, *picked], rejected=rejected, left_out=left_out,
            )

        for position, shared in namesakes.items():
            result = results[position]
            if result is not None:
                status = LinkStatus.AMBIGUOUS if result.status is LinkStatus.NOT_FOUND else result.status
                results[position] = replace(result, status=status, namesakes=shared)
        return Linking(
            results=[result for result in results if result is not None],
            timed_out=found.timed_out,
            verifier_called=bool(to_check) and self._verifier is not None,
            names=names,
        )


def _namesakes(mention: Mention, candidates: list[Candidate], names: dict[str, str]) -> list[Candidate]:
    """The readable people whose full name starts with a first name asked on its
    own, when there are two or more: the name alone cannot tell them apart."""
    if not any(kind is EntityKind.PERSON and len(key.rpartition(":")[2].split()) == 1 for kind, key in mention.keys):
        return []
    people = [
        c for c in candidates
        if c.source is CandidateSource.PREFIX and c.kind == EntityKind.PERSON.value and mention.hinted(c.kind)
        and offerable(mention.name, names.get(c.entity_id, c.name))
    ]
    return people if len(people) >= 2 else []


def record_count_label(candidate: Candidate) -> str:
    """How many readable records mention it, as shown ("1 record", "3+ records")."""
    count = len(candidate.records)
    if candidate.more_records:
        return f"{count}+ records"
    return f"{count} record" if count == 1 else f"{count} records"

from __future__ import annotations

from typing import Any

import pytest

from app.modules.named_entities.linking.candidates import (
    Candidate,
    CandidateSet,
    CandidateSource,
    MentionCandidates,
)
from app.modules.named_entities.linking.mentions import Mention, parse_mentions
from app.modules.named_entities.linking.service import (
    EntityLinker,
    Linking,
    LinkStatus,
    readable_names,
)


def _c(entity_id: str, name: str, kind: str, source: CandidateSource, record: str = "r1") -> Candidate:
    return Candidate(entity_id, kind, name, source, ({"_key": record},), False)


class _Finder:
    def __init__(self, per_mention: list[tuple[list[Candidate], bool]], *, timed_out: bool = False) -> None:
        self.per_mention, self.timed_out = per_mention, timed_out

    async def find(self, mentions: list[Mention]) -> CandidateSet:
        return CandidateSet(
            per_mention=[
                MentionCandidates(mention=m, candidates=c, ambiguous=a) for m, (c, a) in zip(mentions, self.per_mention)
            ],
            timed_out=self.timed_out,
        )


class _Verifier:
    def __init__(self, answer: dict[int, list[int]] | None) -> None:
        self.answer = answer
        self.calls: list[tuple[str, list[Any]]] = []

    async def verify(self, question: str, items: list[Any]) -> dict[int, list[int]] | None:
        self.calls.append((question, items))
        return self.answer


class _Graph:
    def __init__(self, spellings: dict[tuple[str, str], list[str]] | None = None) -> None:
        self.spellings = spellings or {}

    async def get_named_entity_mentions_in_records(
        self, org_id: str, entity_ids: list[str], record_ids: list[str],
    ) -> list[dict[str, Any]]:
        return [
            {"recordId": r, "entityId": e, "extractedNames": names}
            for (e, r), names in self.spellings.items() if e in entity_ids and r in record_ids
        ]


def _mentions(*specs: dict[str, str]) -> list[Mention]:
    return parse_mentions(list(specs)).mentions


async def _link(
    per_mention: list[tuple[list[Candidate], bool]],
    verifier: _Verifier | None = None,
    graph: _Graph | None = None,
    mentions: list[Mention] | None = None,
    timed_out: bool = False,
) -> Linking:
    mentions = mentions or _mentions(*({"name": f"Name {chr(65 + i)}", "kind": "organization"} for i in range(len(per_mention))))
    linker = EntityLinker(_Finder(per_mention, timed_out=timed_out), verifier, graph or _Graph(), "org")
    return await linker.link(mentions, "the question")


@pytest.mark.asyncio
async def test_an_exact_match_of_the_asked_kind_settles_the_name_without_a_check() -> None:
    verifier = _Verifier({0: [0]})
    exact = _c("e1", "Acme", "organization", CandidateSource.EXACT)
    dense = _c("e2", "Acme Labs", "organization", CandidateSource.DENSE)
    linking = await _link([([exact, dense], False)], verifier)
    (result,) = linking.results
    assert result.status is LinkStatus.MATCHED and result.accepted == [exact]
    assert verifier.calls == [] and not linking.verifier_called


@pytest.mark.asyncio
async def test_an_exact_first_name_lets_the_check_add_the_full_names_it_starts() -> None:
    verifier = _Verifier({0: [0]})
    sam = _c("e1", "Sam", "person", CandidateSource.EXACT)
    sam_lee, sam_park = _c("e2", "Sam Lee", "person", CandidateSource.PREFIX), _c("e3", "Sam Park", "person", CandidateSource.PREFIX)
    mentions = _mentions({"name": "Sam", "kind": "person"})
    linking = await _link([([sam, sam_lee, sam_park], False)], verifier, mentions=mentions)
    (result,) = linking.results
    assert result.status is LinkStatus.CHECKED
    assert result.accepted == [sam, sam_lee] and result.rejected == [sam_park]
    ((_, items),) = verifier.calls
    assert [c.name for c in items[0].candidates] == ["Sam Lee", "Sam Park"], "the exact node is not offered"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, {0: []}])
async def test_a_failed_or_empty_check_never_drops_the_exact_match(answer: dict[int, list[int]] | None) -> None:
    sam = _c("e1", "Sam", "person", CandidateSource.EXACT)
    sam_lee = _c("e2", "Sam Lee", "person", CandidateSource.PREFIX)
    mentions = _mentions({"name": "Sam", "kind": "person"})
    linking = await _link([([sam, sam_lee], False)], _Verifier(answer), mentions=mentions)
    (result,) = linking.results
    assert result.status is LinkStatus.MATCHED and result.accepted == [sam]
    if answer is None:
        assert result.unchecked == [sam_lee], "a check that never answered says what it left out"
    else:
        assert result.rejected == [sam_lee] and result.unchecked == []


@pytest.mark.asyncio
async def test_an_exact_match_beside_too_many_longer_names_says_so() -> None:
    verifier = _Verifier({0: [0]})
    sam = _c("e1", "Sam", "person", CandidateSource.EXACT)
    mentions = _mentions({"name": "Sam", "kind": "person"})
    linking = await _link([([sam], True)], verifier, mentions=mentions)
    (result,) = linking.results
    assert result.status is LinkStatus.MATCHED and result.accepted == [sam]
    assert result.more_longer_names is True and verifier.calls == []


@pytest.mark.asyncio
async def test_an_exact_match_of_another_kind_goes_to_the_check() -> None:
    verifier = _Verifier({0: []})
    product = _c("e1", "Atlas", "product", CandidateSource.EXACT)
    mentions = _mentions({"name": "Atlas", "kind": "organization"})
    linking = await _link([([product], False)], verifier, mentions=mentions)
    assert linking.results[0].status is LinkStatus.NOT_FOUND
    assert linking.results[0].rejected == [product]
    assert len(verifier.calls) == 1


@pytest.mark.asyncio
async def test_one_check_for_every_unsettled_name_and_its_choices_are_applied() -> None:
    verifier = _Verifier({0: [], 1: [1]})
    lookalike = _c("e1", "Acme Financial", "organization", CandidateSource.DENSE)
    a, b = _c("e2", "Platform", "product", CandidateSource.DENSE), _c("e3", "Globex Systems Inc", "organization", CandidateSource.DENSE)
    linking = await _link([([lookalike], False), ([a, b], False)], verifier)
    first, second = linking.results
    assert (first.status, first.rejected) == (LinkStatus.NOT_FOUND, [lookalike])
    assert (second.status, second.accepted, second.rejected) == (LinkStatus.CHECKED, [b], [a])
    ((question, items),) = verifier.calls
    assert question == "the question" and [len(item.candidates) for item in items] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, {}])
async def test_without_an_answer_nothing_unchecked_is_used(answer: dict[int, list[int]] | None) -> None:
    dense = _c("e1", "Acme Labs", "organization", CandidateSource.DENSE)
    linking = await _link([([dense], False)], _Verifier(answer))
    (result,) = linking.results
    assert result.status is LinkStatus.UNVERIFIED and result.accepted == [] and result.unchecked == [dense]


@pytest.mark.asyncio
async def test_no_verifier_leaves_unsettled_names_unverified() -> None:
    linking = await _link([([_c("e1", "Acme Labs", "organization", CandidateSource.DENSE)], False)], None)
    assert linking.results[0].status is LinkStatus.UNVERIFIED and not linking.verifier_called


@pytest.mark.asyncio
async def test_ambiguous_and_not_found_need_no_check() -> None:
    verifier = _Verifier({})
    linking = await _link([([], True), ([], False)], verifier)
    assert [r.status for r in linking.results] == [LinkStatus.AMBIGUOUS, LinkStatus.NOT_FOUND]
    assert verifier.calls == []


@pytest.mark.asyncio
async def test_a_candidate_differing_by_a_digit_is_rejected_without_being_offered() -> None:
    verifier = _Verifier({0: [0]})
    mentions = _mentions({"name": "Q3 2024 plan", "kind": "event"})
    q4 = _c("e1", "Q4 2024 plan", "event", CandidateSource.DENSE)
    linking = await _link([([q4], False)], verifier, mentions=mentions)
    assert linking.results[0].status is LinkStatus.NOT_FOUND and linking.results[0].rejected == [q4]
    assert verifier.calls == []


@pytest.mark.asyncio
async def test_names_come_from_readable_records_spellings() -> None:
    graph = _Graph({("e1", "r1"): ["ACME", "Acme Corp", "ACME"]})
    names = await readable_names(graph, "org", [_c("e1", "acme stored", "organization", CandidateSource.EXACT)])
    assert names == {"e1": "ACME"}
    assert await readable_names(_Graph(), "org", [_c("e2", "Globex", "organization", CandidateSource.EXACT)]) == {"e2": "Globex"}


@pytest.mark.asyncio
async def test_a_timeout_in_finding_candidates_is_carried_to_the_caller() -> None:
    linking = await _link([([], False)], timed_out=True)
    assert linking.timed_out

from __future__ import annotations

import itertools
from typing import Any

import pytest

from app.modules.named_entities.linking import records as records_module
from app.modules.named_entities.linking.candidates import Candidate, CandidateSource
from app.modules.named_entities.linking.mentions import parse_mentions
from app.modules.named_entities.linking.records import (
    NEARBY,
    SAME_BLOCK,
    SAME_RECORD,
    co_mention,
    readable_records,
)
from app.modules.named_entities.linking.service import LinkResult, LinkStatus
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityRecordPage,
)

CONTEXT = EntityAccessContext(
    org_id="org", user_key="u", app_level_app_ids=frozenset({"kb"}), record_level_app_ids=frozenset(),
    record_group_ids=frozenset(), app_names={},
)


@pytest.mark.parametrize(("blocks", "expected"), [
    ([{3}, {3}], (SAME_BLOCK, 3)),
    ([{3}, {5}], (NEARBY, 3)),
    ([{3}, {1}], (NEARBY, 3)),
    ([{3}, {6}], (SAME_RECORD, -1)),
    ([{1, 9}, {9}, {8}], (NEARBY, 9)),
    ([{1, 9}, {9}, {9}], (SAME_BLOCK, 9)),
    ([{1}, set()], (SAME_RECORD, -1)),
    ([], (SAME_RECORD, -1)),
])
def test_co_mention_tiers(blocks: list[set[int]], expected: tuple[int, int]) -> None:
    assert co_mention(blocks) == expected


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_the_tier_does_not_depend_on_name_order(order: tuple[int, ...]) -> None:
    blocks = [{2, 40}, {4}, {41, 3}]
    assert co_mention([blocks[i] for i in order])[0] == NEARBY


def _link(name: str, *entity_ids: str) -> LinkResult:
    (mention,) = parse_mentions([{"name": name, "kind": "organization"}]).mentions
    accepted = [Candidate(e, "organization", e, CandidateSource.EXACT, (), False) for e in entity_ids]
    return LinkResult(mention, LinkStatus.MATCHED, accepted=accepted)


class _Graph:
    def __init__(self, mentions: dict[str, dict[str, list[int]]]) -> None:
        """``{entity_id: {record_id: block indexes}}``; every record is readable."""
        self.mentions = mentions
        self.within_calls: list[tuple[list[str], list[str]]] = []

    async def get_records_for_named_entities(
        self, org_id: str, entity_ids: list[str] | None = None, query: object = None,
        limit: int = 5000, within: list[str] | None = None,
    ) -> dict[str, Any]:
        self.within_calls.append((list(entity_ids or []), list(within or [])))
        hits = [r for r in within or [] if any(r in self.mentions.get(e, {}) for e in entity_ids or [])]
        return {"hits": [{"recordId": r, "virtualRecordId": f"v-{r}"} for r in hits], "truncated": False}

    async def get_named_entity_mentions_in_records(
        self, org_id: str, entity_ids: list[str], record_ids: list[str],
    ) -> list[dict[str, Any]]:
        return [
            {"recordId": r, "entityId": e, "blockIndexes": self.mentions[e][r], "extractedNames": [e], "mentionCount": 1}
            for e in entity_ids for r in record_ids if r in self.mentions.get(e, {})
        ]


@pytest.fixture
def listings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``list_accessible_entity_records`` over the fake graph, capped at ``cap``."""
    state: dict[str, Any] = {"cap": 100}

    async def _list(
        graph: _Graph, context: EntityAccessContext, *, entity_id: str, entity_type: str, limit: int,
    ) -> EntityRecordPage:
        keys = sorted(graph.mentions.get(entity_id, {}))
        shown = keys[: min(limit, state["cap"])]
        rows = [{"_key": k, "virtualRecordId": f"v-{k}"} for k in shown]
        return EntityRecordPage(records=rows, next_cursor="more" if len(keys) > len(shown) else None)

    monkeypatch.setattr(records_module, "list_accessible_entity_records", _list)
    return state


@pytest.mark.asyncio
async def test_any_is_the_union_with_counts_per_name(listings: dict[str, Any]) -> None:
    graph = _Graph({"acme": {"r1": [0], "r2": [0]}, "globex": {"r2": [1], "r3": [1]}})
    scope = await readable_records(graph, CONTEXT, [_link("Acme", "acme"), _link("Globex", "globex")], match_all=False)
    assert scope.record_ids == ["r1", "r2", "r3"]
    assert scope.virtual_record_ids == ["v-r1", "v-r2", "v-r3"]
    assert [n.count_label for n in scope.per_name] == ["2", "2"]
    assert graph.within_calls == []


@pytest.mark.asyncio
async def test_all_starts_from_the_rarest_name_and_matches_the_others_inside_it(listings: dict[str, Any]) -> None:
    popular = {f"p{i}": [0] for i in range(50)}
    graph = _Graph({"northwind": {**popular, "r1": [4], "r2": [9]}, "contoso": {"r1": [5], "r2": [30], "r9": [0]}})
    links = [_link("Northwind", "northwind"), _link("Contoso", "contoso")]
    scope = await readable_records(graph, CONTEXT, links, match_all=True)
    assert graph.within_calls == [(["northwind"], ["r1", "r2", "r9"])]
    assert sorted(scope.record_ids) == ["r1", "r2"]
    assert scope.together == {"r1": (NEARBY, 4), "r2": (SAME_RECORD, -1)}
    assert scope.record_ids == ["r1", "r2"], "nearest first"


@pytest.mark.asyncio
async def test_all_keeps_every_record_that_mentions_them_all_nearest_first(listings: dict[str, Any]) -> None:
    near = {f"n{i}": [i * 10] for i in range(4)}
    graph = _Graph({
        "a": {**near, "far": [0], "no-blocks": []},
        "b": {**{k: [v[0] + 1] for k, v in near.items()}, "far": [50], "no-blocks": []},
    })
    scope = await readable_records(graph, CONTEXT, [_link("A", "a"), _link("B", "b")], match_all=True)
    assert sorted(scope.record_ids[:4]) == sorted(near)
    assert set(scope.record_ids[4:]) == {"far", "no-blocks"}, "farther co-mentions are searched too"
    assert scope.note == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("unlinked", [LinkStatus.NOT_FOUND, LinkStatus.UNVERIFIED, LinkStatus.AMBIGUOUS])
async def test_all_with_a_name_that_did_not_link_selects_nothing_but_keeps_the_counts(
    listings: dict[str, Any], unlinked: LinkStatus,
) -> None:
    graph = _Graph({"a": {"r1": [0]}, "b": {"r1": [0], "r2": [0]}})
    (mention,) = parse_mentions([{"name": "Initech", "kind": "organization"}]).mentions
    links = [_link("A", "a"), LinkResult(mention, unlinked), _link("B", "b")]
    scope = await readable_records(graph, CONTEXT, links, match_all=True)
    assert scope.record_ids == [] and scope.virtual_record_ids == []
    assert [(n.mention, n.count_label) for n in scope.per_name] == [("A", "1"), ("B", "2")]
    assert graph.within_calls == []
    any_scope = await readable_records(graph, CONTEXT, links, match_all=False)
    assert any_scope.record_ids == ["r1", "r2"], "any still searches the names that linked"


@pytest.mark.asyncio
async def test_all_is_a_subset_of_any(listings: dict[str, Any]) -> None:
    graph = _Graph({"a": {"r1": [0], "r2": [0], "r3": [0]}, "b": {"r2": [0], "r3": [7], "r4": [0]}})
    links = [_link("A", "a"), _link("B", "b")]
    every = await readable_records(graph, CONTEXT, links, match_all=True)
    some = await readable_records(graph, CONTEXT, links, match_all=False)
    assert set(every.record_ids) <= set(some.record_ids)


@pytest.mark.asyncio
async def test_no_record_with_every_name_is_an_empty_scope(listings: dict[str, Any]) -> None:
    graph = _Graph({"a": {"r1": [0]}, "b": {"r2": [0]}})
    scope = await readable_records(graph, CONTEXT, [_link("A", "a"), _link("B", "b")], match_all=True)
    assert scope.record_ids == [] and scope.virtual_record_ids == []


@pytest.mark.asyncio
async def test_a_name_listed_only_in_part_says_so(listings: dict[str, Any]) -> None:
    listings["cap"] = 2
    graph = _Graph({"a": {f"r{i}": [0] for i in range(5)}, "b": {f"r{i}": [0] for i in range(5)}})
    scope = await readable_records(graph, CONTEXT, [_link("A", "a"), _link("B", "b")], match_all=True)
    assert [n.count_label for n in scope.per_name] == ["2+", "2+"]
    assert "only the newest were intersected" in scope.note


@pytest.mark.asyncio
async def test_any_says_when_a_popular_name_was_searched_only_in_its_newest_records(listings: dict[str, Any]) -> None:
    listings["cap"] = 2
    graph = _Graph({"a": {f"r{i}": [0] for i in range(5)}, "b": {"r9": [0]}})
    scope = await readable_records(graph, CONTEXT, [_link("A", "a"), _link("B", "b")], match_all=False)
    assert [n.count_label for n in scope.per_name] == ["2+", "1"]
    assert "'A': more than" in scope.note and "only the newest were searched" in scope.note
    assert "'B'" not in scope.note


@pytest.mark.asyncio
async def test_a_complete_listing_adds_no_note(listings: dict[str, Any]) -> None:
    graph = _Graph({"a": {"r1": [0]}})
    scope = await readable_records(graph, CONTEXT, [_link("A", "a")], match_all=False)
    assert scope.note == "" and scope.record_names == {"r1": ""}


@pytest.mark.asyncio
async def test_the_scope_cap_keeps_every_name_and_says_what_it_cut(
    listings: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(records_module, "MAX_SCOPE_RECORDS", 4)
    graph = _Graph({"a": {f"a{i}": [0] for i in range(6)}, "b": {"b1": [0], "b2": [0]}})
    scope = await readable_records(graph, CONTEXT, [_link("A", "a"), _link("B", "b")], match_all=False)
    assert len(scope.record_ids) == 4 and {"b1", "b2"} <= set(scope.record_ids)
    assert "Only 4 of the 8 readable records were searched." in scope.note

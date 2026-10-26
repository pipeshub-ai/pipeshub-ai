from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any

import pytest

from app.modules.named_entities.keys import named_entity_key
from app.modules.named_entities.linking.candidates import (
    MAX_CANDIDATES,
    CandidateFinder,
    CandidateSet,
    CandidateSource,
)
from app.modules.named_entities.linking.mentions import parse_mentions
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityHit,
    EntityProbe,
    EntitySearchTimedOut,
    PermittedEntity,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

ORG = "org-1"
CONTEXT = EntityAccessContext(
    org_id=ORG, user_key="u", app_level_app_ids=frozenset({"kb"}), record_level_app_ids=frozenset(),
    record_group_ids=frozenset(), app_names={"kb": "KB"},
)


def _node(kind: str, key: str, name: str) -> dict[str, Any]:
    return {"id": named_entity_key(ORG, kind, key), "kind": kind, "name": name, "normKey": key}


class _Graph:
    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes = {node["id"]: node for node in nodes}
        self.prefix_calls: list[list[tuple[str, str]]] = []

    async def get_named_entities_by_ids(self, org_id: str, ids: list[str]) -> list[dict[str, Any]]:
        return [self.nodes[i] for i in ids if i in self.nodes]

    async def find_named_entities_by_key_prefix(self, org_id: str, prefixes, limit: int) -> list[dict[str, Any]]:
        self.prefix_calls.append(list(prefixes))
        rows = []
        for kind, key in prefixes:
            matched = [n for n in self.nodes.values() if n["kind"] == kind and n["normKey"].startswith(f"{key} ")]
            rows += [{**n, "key": key} for n in sorted(matched, key=lambda n: n["normKey"])[:limit]]
        return rows


def _probe(readable: set[str], *, timed_out: bool = False) -> Callable[..., Awaitable[EntityProbe]]:
    seen: list[list[tuple[str, str]]] = []

    async def probe(graph: object, context: object, entities: list[tuple[str, str]]) -> EntityProbe:
        seen.append(list(entities))
        return EntityProbe(
            permitted={
                entity_id: PermittedEntity(entity_id, kind, [{"_key": f"rec-{entity_id[:4]}"}], False)
                for entity_id, kind in entities if entity_id in readable
            },
            timed_out=timed_out,
        )

    probe.seen = seen  # type: ignore[attr-defined]
    return probe


def _hit(entity_id: str, name: str, kind: str = "organization") -> EntityHit:
    return EntityHit(entity_id, "named_entity", name, 0.5, [{"_key": "rec-d"}], False, kind=kind)


async def _find(
    nodes: list[dict[str, Any]], readable: set[str], mentions: list[Any], *,
    dense: Callable[..., Awaitable[list[EntityHit]]] | None = None, timed_out: bool = False,
) -> tuple[_Graph, CandidateSet]:
    graph = _Graph(nodes)
    finder = CandidateFinder(graph, CONTEXT, dense=dense, probe=_probe(readable, timed_out=timed_out))
    return graph, await finder.find(parse_mentions(mentions).mentions)


@pytest.mark.asyncio
async def test_a_readable_exact_node_is_an_exact_candidate() -> None:
    acme = _node("organization", "name:organization:acme", "Acme")
    _, found = await _find([acme], {acme["id"]}, [{"name": "Acme Inc.", "kind": "organization"}])
    (entry,) = found.per_mention
    assert [(c.entity_id, c.source, c.kind) for c in entry.candidates] == [(acme["id"], CandidateSource.EXACT, "organization")]
    assert entry.candidates[0].records == ({"_key": f"rec-{acme['id'][:4]}"},)


@pytest.mark.asyncio
async def test_an_unreadable_node_is_no_candidate_at_all() -> None:
    acme = _node("organization", "name:organization:acme", "Acme")
    _, unreadable = await _find([acme], set(), [{"name": "Acme", "kind": "organization"}])
    _, missing = await _find([], set(), [{"name": "Acme", "kind": "organization"}])
    assert unreadable.per_mention[0].candidates == missing.per_mention[0].candidates == []
    assert unreadable.per_mention[0].ambiguous is missing.per_mention[0].ambiguous is False


@pytest.mark.asyncio
async def test_a_prefix_reaches_full_names_on_word_boundaries() -> None:
    sam_lee = _node("person", "name:person:sam lee", "Sam Lee")
    samantha = _node("person", "name:person:samantha", "Samantha")
    _, found = await _find([sam_lee, samantha], {sam_lee["id"], samantha["id"]}, [{"name": "Sam", "kind": "person"}])
    assert [c.name for c in found.per_mention[0].candidates] == ["Sam Lee"]
    assert found.per_mention[0].candidates[0].source is CandidateSource.PREFIX


@pytest.mark.asyncio
async def test_short_names_and_other_kinds_run_no_prefix_lookup() -> None:
    graph, _ = await _find([], set(), [{"name": "Al", "kind": "person"}, {"name": "Paris", "kind": "city"}])
    assert graph.prefix_calls == [[]]


@pytest.mark.asyncio
async def test_more_readable_prefix_matches_than_can_be_told_apart_is_ambiguous() -> None:
    sams = [_node("person", f"name:person:sam {i}", f"Sam {i}") for i in range(MAX_CANDIDATES + 1)]
    _, found = await _find(sams, {n["id"] for n in sams}, [{"name": "Sam", "kind": "person"}])
    assert found.per_mention[0].ambiguous and found.per_mention[0].candidates == []
    # The same nodes, mostly unreadable: what decides is what this user can read.
    _, found = await _find(sams, {sams[0]["id"]}, [{"name": "Sam", "kind": "person"}])
    assert not found.per_mention[0].ambiguous
    assert [c.name for c in found.per_mention[0].candidates] == ["Sam 0"]


@pytest.mark.asyncio
async def test_dense_runs_only_for_embedded_kinds_and_never_repeats_an_exact_candidate() -> None:
    acme = _node("organization", "name:organization:acme", "Acme")
    calls: list[tuple[str, list[str]]] = []

    async def dense(name: str, kinds: list[str]) -> list[EntityHit]:
        calls.append((name, kinds))
        return [_hit(acme["id"], "Acme"), _hit("other", "Acme Labs")]

    _, found = await _find([acme], {acme["id"]}, [{"name": "Acme", "kind": "organization"}, {"name": "Sam Lee", "kind": "person"}], dense=dense)
    assert calls == [("Acme", ["organization"])]
    assert [(c.name, c.source) for c in found.per_mention[0].candidates] == [
        ("Acme", CandidateSource.EXACT), ("Acme Labs", CandidateSource.DENSE),
    ]


@pytest.mark.asyncio
async def test_a_dense_timeout_is_reported_once_and_drops_only_dense() -> None:
    acme = _node("organization", "name:organization:acme", "Acme")

    async def dense(name: str, kinds: list[str]) -> list[EntityHit]:
        raise EntitySearchTimedOut("late")

    _, found = await _find([acme], {acme["id"]}, [{"name": "Acme", "kind": "organization"}], dense=dense)
    assert found.timed_out
    assert [c.source for c in found.per_mention[0].candidates] == [CandidateSource.EXACT]


@pytest.mark.asyncio
async def test_at_most_five_candidates_besides_the_exact_one_exact_first() -> None:
    acme = _node("organization", "name:organization:acme", "Acme")

    async def dense(name: str, kinds: list[str]) -> list[EntityHit]:
        return [_hit(f"d{i}", f"Acme {i}") for i in range(8)]

    _, found = await _find([acme], {acme["id"]}, [{"name": "Acme", "kind": "organization"}], dense=dense)
    candidates = found.per_mention[0].candidates
    assert len(candidates) == MAX_CANDIDATES + 1 and candidates[0].source is CandidateSource.EXACT


@pytest.mark.asyncio
@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
async def test_each_mention_gets_the_same_candidates_whatever_the_order(order: tuple[int, ...]) -> None:
    nodes = [
        _node("organization", "name:organization:acme", "Acme"),
        _node("person", "name:person:sam lee", "Sam Lee"),
        _node("product", "name:product:atlas", "Atlas"),
    ]
    mentions = [{"name": "Acme", "kind": "organization"}, {"name": "Sam", "kind": "person"}, {"name": "Atlas"}]
    _, found = await _find(nodes, {n["id"] for n in nodes}, [mentions[i] for i in order])
    by_name = {e.mention.name: [c.entity_id for c in e.candidates] for e in found.per_mention}
    assert by_name == {"Acme": [nodes[0]["id"]], "Sam": [nodes[1]["id"]], "Atlas": [nodes[2]["id"]]}


@pytest.mark.asyncio
async def test_unreadable_prefix_matches_past_the_row_limit_hide_nothing_and_decide_nothing() -> None:
    """The prefix read is bounded before the access check, so a readable match
    behind more unreadable ones than the limit is missed: never shown, and the
    unreadable ones never make the name ambiguous."""
    from app.modules.named_entities.linking.candidates import PREFIX_ROW_LIMIT

    hidden = [_node("person", f"name:person:sam a{i:02d}", f"Sam A{i:02d}") for i in range(PREFIX_ROW_LIMIT + 4)]
    readable = _node("person", "name:person:sam zed", "Sam Zed")
    _, found = await _find([*hidden, readable], {readable["id"]}, [{"name": "Sam", "kind": "person"}])
    (entry,) = found.per_mention
    assert entry.candidates == [] and entry.ambiguous is False


@pytest.mark.asyncio
async def test_an_exact_node_does_not_push_a_longer_name_out_of_the_cap() -> None:
    sam = _node("person", "name:person:sam", "Sam")
    longer = [_node("person", f"name:person:sam {c}", f"Sam {c}") for c in "abcde"]
    _, found = await _find([sam, *longer], {n["id"] for n in [sam, *longer]}, [{"name": "Sam", "kind": "person"}])
    (entry,) = found.per_mention
    assert entry.candidates[0].source is CandidateSource.EXACT
    assert [c.name for c in entry.candidates[1:]] == [f"Sam {c}" for c in "abcde"]
    assert entry.ambiguous is False

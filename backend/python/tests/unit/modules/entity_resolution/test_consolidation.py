"""Taxonomy consolidation (KG-33, B7): reversible merges of duplicate nodes
within an org, and moving an org's records off legacy nodes (no ``orgId``)
onto its canonical per-org nodes.

A fake graph keeps nodes, record edges and record orgs, so each test can
assert the graph state a merge or a migration leaves behind, and that the
reverse operation restores it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityType
from app.modules.entity_resolution.consolidation import (
    TaxonomyConsolidator,
)
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.normalizer import normalize_name
from app.modules.transformers.entity_vectorstore import EntityWriteOutcome

if TYPE_CHECKING:
    from collections.abc import Callable

TOPICS = CollectionNames.TOPICS.value
SUB1 = CollectionNames.SUBCATEGORIES1.value
ORG, OTHER = "org-1", "org-2"
LOGGER = logging.getLogger("consolidation-test")


@dataclass
class Edge:
    record: str
    target: tuple[str, str]
    extracted_name: str | None = None
    merged_from: str | None = None
    migrated_from: str | None = None


@dataclass
class FakeGraph:
    nodes: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    record_orgs: dict[str, str] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)

    def node(self, collection: str, key: str, name: str, org: str | None, *,
             created: int = 1, aliases: list[str] | None = None, **extra: object) -> None:
        doc: dict[str, Any] = {"name": name, "createdAtTimestamp": created, "aliases": aliases or []}
        if org is not None:
            doc |= {"orgId": org, "normalizedName": normalize_name(name)}
        self.nodes[(collection, key)] = doc | extra

    def link(self, record: str, org: str, collection: str, key: str, extracted: str | None = None) -> None:
        self.record_orgs[record] = org
        self.edges.append(Edge(record, (collection, key), extracted))

    def targets(self, record: str) -> list[tuple[str, str]]:
        return sorted(e.target for e in self.edges if e.record == record)

    # ---- provider surface ----
    async def page_entity_index_source(self, source: str, scope_id: str, after_key: str | None,
                                       limit: int, transaction: str | None = None) -> list[dict]:
        rows = [
            {"_key": k, "name": n["name"], "aliases": list(n.get("aliases") or []),
             "createdAtTimestamp": n.get("createdAtTimestamp")}
            for (c, k), n in sorted(self.nodes.items())
            if c == source and n.get("orgId") == scope_id and n.get("normalizedName")
            and not n.get("mergedInto") and (after_key is None or k > after_key)
        ]
        return rows[:limit]

    async def get_nodes_by_field_in(self, collection: str, field_name: str, values: list[Any],
                                    return_fields: list[str] | None = None,
                                    transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        wanted = set(values)
        return [
            {"id": k, **{f: n.get(f) for f in (return_fields or []) if f != "id"}}
            for (c, k), n in sorted(self.nodes.items())
            if c == collection and (k if field_name == "id" else n.get(field_name)) in wanted
        ]

    async def find_taxonomy_nodes(self, collection: str, org_id: str, names: list[str],
                                  transaction: str | None = None) -> list[dict]:
        wanted = set(names)
        return [
            {"id": k, "name": n["name"], "normalizedName": n.get("normalizedName"),
             "aliases": list(n.get("aliases") or [])}
            for (c, k), n in sorted(self.nodes.items())
            if c == collection and n.get("orgId") == org_id and not n.get("mergedInto")
            and (n.get("normalizedName") in wanted or wanted & set(n.get("normalizedAliases") or []))
        ]

    async def add_taxonomy_aliases(self, collection: str, key: str, aliases: list[str],
                                   normalized: list[str], *, org_id: str, max_aliases: int = 20,
                                   transaction: str | None = None) -> None:
        node = self.nodes.get((collection, key))
        if node is None or node.get("orgId") != org_id:
            return
        self.writes.append(f"aliases:{key}")
        current = list(node.get("aliases") or [])
        current_norm = list(node.get("normalizedAliases") or [])
        for a, n in zip(aliases, normalized):
            if n not in current_norm:
                current.append(a)
                current_norm.append(n)
        node["aliases"], node["normalizedAliases"] = current[:max_aliases], current_norm[:max_aliases]

    async def move_taxonomy_edges(self, collection: str, from_key: str, to_key: str, org_id: str, *,
                                  set_merged_from: str | None, only_merged_from: str | None = None,
                                  provenance: str = "mergedFrom",
                                  dry_run: bool = False, transaction: str | None = None) -> int:
        field_name = "merged_from" if provenance == "mergedFrom" else "migrated_from"
        if not dry_run and (collection, to_key) not in self.nodes:
            raise ValueError(f"{collection}/{to_key} not found")
        moved = 0
        for edge in list(self.edges):
            if edge.target != (collection, from_key) or self.record_orgs.get(edge.record) != org_id:
                continue
            if only_merged_from is not None and getattr(edge, field_name) != only_merged_from:
                continue
            moved += 1
            if dry_run:
                continue
            if (collection, to_key) in self.targets(edge.record):
                self.edges.remove(edge)
                continue
            edge.target = (collection, to_key)
            # Like both providers: a forward move keeps the first origin; a
            # restore clears it, and a return to a legacy node clears both.
            if only_merged_from is None:
                setattr(edge, field_name, getattr(edge, field_name) or set_merged_from)
            else:
                setattr(edge, field_name, set_merged_from)
                if provenance == "migratedFrom":
                    edge.merged_from = None
        if moved and not dry_run:
            self.writes.append(f"move:{from_key}->{to_key}")
        return moved

    async def update_node(self, key: str, collection: str, updates: dict) -> bool:
        self.writes.append(f"update:{key}")
        node = self.nodes[(collection, key)]
        for f, v in updates.items():
            if v is None:
                node.pop(f, None)
            else:
                node[f] = v
        return True

    async def create_taxonomy_node_if_absent(self, collection: str, node: dict,
                                             transaction: str | None = None) -> None:
        key = (collection, node["id"])
        if key not in self.nodes:
            self.writes.append(f"create:{node['id']}")
            self.nodes[key] = {k: v for k, v in node.items() if k != "id"} | {"aliases": []}

    async def find_legacy_taxonomy_nodes(self, collection: str, org_id: str, limit: int,
                                         after_key: str | None = None,
                                         transaction: str | None = None) -> list[dict]:
        counts: dict[str, int] = {}
        for edge in self.edges:
            c, k = edge.target
            node = self.nodes.get(edge.target)
            if c == collection and node is not None and node.get("orgId") is None \
                    and self.record_orgs.get(edge.record) == org_id:
                counts[k] = counts.get(k, 0) + 1
        return [{"_key": k, "name": self.nodes[(collection, k)]["name"], "records": n}
                for k, n in sorted(counts.items()) if after_key is None or k > after_key][:limit]

    async def get_taxonomy_entity_membership(self, refs: list[dict], org_id: str,
                                             transaction: str | None = None) -> dict:
        out = {}
        for ref in refs:
            connectors = sorted({
                f"c-{e.record}" for e in self.edges
                if e.target[1] == ref["id"] and self.record_orgs.get(e.record) == org_id
            })
            out[(ref["type"], ref["id"])] = {"connectorIds": connectors, "recordGroupIds": []}
        return out


class FakeStore:
    def __init__(self) -> None:
        self.upserts: list[Any] = []
        self.deletes: list[tuple[str, str, list[str]]] = []

    async def upsert_entities_batch(self, entities: list, batch_size: int = 64, *,
                                    merge_membership: bool = True) -> EntityWriteOutcome:
        self.upserts.extend(entities)
        return EntityWriteOutcome(written=len(entities))

    async def delete_entities(self, org_id: str, entity_type: str, entity_ids: list[str]) -> None:
        self.deletes.append((org_id, entity_type, sorted(entity_ids)))


def _consolidator(graph: FakeGraph, store: FakeStore | None = None) -> TaxonomyConsolidator:
    return TaxonomyConsolidator(graph_provider=graph, entity_store=store, logger=LOGGER, now_ms=lambda: 777)


# ---------------------------------------------------------------------------
# Duplicate groups
# ---------------------------------------------------------------------------


class TestDuplicateGroups:
    async def test_same_spelling_key_is_one_group_with_the_oldest_as_winner(self) -> None:
        graph = FakeGraph()
        graph.node(TOPICS, "a", "Bug bash", ORG, created=30)
        graph.node(TOPICS, "b", "bug-bash", ORG, created=10)
        graph.node(TOPICS, "c", "Bug Bash!", ORG, created=20)
        graph.node(TOPICS, "d", "Bug bashes", ORG, created=5)
        (group,) = await _consolidator(graph).duplicate_groups(TOPICS, ORG)
        assert group.winner.key == "b"
        assert [n.key for n in group.losers] == ["c", "a"]

    async def test_tie_on_age_breaks_by_key(self) -> None:
        graph = FakeGraph()
        graph.node(TOPICS, "z", "Q3 plan", ORG, created=1)
        graph.node(TOPICS, "y", "q3-plan", ORG, created=1)
        (group,) = await _consolidator(graph).duplicate_groups(TOPICS, ORG)
        assert group.winner.key == "y"

    async def test_words_that_differ_are_not_grouped(self) -> None:
        graph = FakeGraph()
        graph.node(TOPICS, "a", ".NET", ORG)
        graph.node(TOPICS, "b", "NET", ORG)
        graph.node(TOPICS, "c", "C++", ORG)
        graph.node(TOPICS, "d", "C#", ORG)
        assert await _consolidator(graph).duplicate_groups(TOPICS, ORG) == []

    async def test_other_orgs_and_merged_nodes_are_ignored(self) -> None:
        graph = FakeGraph()
        graph.node(TOPICS, "a", "Pricing", ORG)
        graph.node(TOPICS, "b", "pricing", OTHER)
        graph.node(TOPICS, "c", "PRICING", ORG, mergedInto="a")
        assert await _consolidator(graph).duplicate_groups(TOPICS, ORG) == []

    async def test_groups_span_pages(self) -> None:
        graph = FakeGraph()
        for i in range(5):
            graph.node(TOPICS, f"k{i}", "Pricing" if i % 2 else "pricing!", ORG, created=i)
        groups = await TaxonomyConsolidator(
            graph_provider=graph, entity_store=None, logger=LOGGER, page_size=2,
        ).duplicate_groups(TOPICS, ORG)
        (group,) = groups
        assert group.winner.key == "k0" and len(group.losers) == 4

    async def test_not_a_taxonomy_collection_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            await _consolidator(FakeGraph()).duplicate_groups("records", ORG)


# ---------------------------------------------------------------------------
# Merge and unmerge
# ---------------------------------------------------------------------------


def _merge_fixture() -> FakeGraph:
    graph = FakeGraph()
    graph.node(TOPICS, "win", "Bug bash", ORG, created=1, aliases=["bugbash day"])
    graph.node(TOPICS, "lose", "bug-bash", ORG, created=2, aliases=["bug bash event"])
    graph.link("r1", ORG, TOPICS, "lose", extracted="bug-bash")
    graph.link("r2", ORG, TOPICS, "lose", extracted="Bug-Bash")
    graph.link("r2", ORG, TOPICS, "win", extracted="Bug bash")  # already on both
    graph.link("r3", ORG, TOPICS, "win")
    return graph


class TestMerge:
    async def test_dry_run_reports_and_writes_nothing(self) -> None:
        graph = _merge_fixture()
        result = await _consolidator(graph).merge(TOPICS, ORG, "win", "lose", dry_run=True)
        assert result.edges_moved == 2 and result.dry_run
        assert graph.writes == []

    async def test_merge_moves_edges_and_redirects_the_loser(self) -> None:
        graph, store = _merge_fixture(), FakeStore()
        result = await _consolidator(graph, store).merge(TOPICS, ORG, "win", "lose", dry_run=False)
        assert result.edges_moved == 2
        assert graph.targets("r1") == [(TOPICS, "win")]
        assert graph.targets("r2") == [(TOPICS, "win")]  # deduped, not doubled
        moved = next(e for e in graph.edges if e.record == "r1")
        assert moved.merged_from == "lose" and moved.extracted_name == "bug-bash"
        loser = graph.nodes[(TOPICS, "lose")]
        assert loser["mergedInto"] == "win" and loser["mergedAt"] == 777
        assert loser["name"] == "bug-bash"  # kept, so the merge can be undone

    async def test_winner_learns_the_losers_spellings(self) -> None:
        graph = _merge_fixture()
        await _consolidator(graph).merge(TOPICS, ORG, "win", "lose", dry_run=False)
        aliases = graph.nodes[(TOPICS, "win")]["aliases"]
        assert "bug-bash" in aliases and "bug bash event" in aliases

    async def test_edges_are_moved_before_the_loser_is_marked(self) -> None:
        """A crash between the two leaves an unmarked loser that a re-run
        finishes; marked first, its remaining edges would be stranded."""
        graph = _merge_fixture()
        await _consolidator(graph).merge(TOPICS, ORG, "win", "lose", dry_run=False)
        assert graph.writes.index("move:lose->win") < graph.writes.index("update:lose")

    async def test_entity_points_follow_the_merge(self) -> None:
        graph, store = _merge_fixture(), FakeStore()
        await _consolidator(graph, store).merge(TOPICS, ORG, "win", "lose", dry_run=False)
        assert (ORG, "topic", ["lose"]) in store.deletes
        (winner,) = [e for e in store.upserts if e.entity_id == "win"]
        assert winner.entity_type is EntityType.TOPIC
        assert winner.connector_ids == ["c-r1", "c-r2", "c-r3"]

    async def test_rerun_on_a_merged_loser_moves_stragglers_only(self) -> None:
        graph = _merge_fixture()
        consolidator = _consolidator(graph)
        await consolidator.merge(TOPICS, ORG, "win", "lose", dry_run=False)
        graph.link("r9", ORG, TOPICS, "lose")  # linked while the merge ran
        result = await consolidator.merge(TOPICS, ORG, "win", "lose", dry_run=False)
        assert result.edges_moved == 1 and graph.targets("r9") == [(TOPICS, "win")]

    @pytest.mark.parametrize(
        ("winner", "loser", "setup"),
        [
            ("win", "win", None),
            ("win", "missing", None),
            ("win", "other-org", lambda g: g.node(TOPICS, "other-org", "bug bash", OTHER)),
            ("win", "legacy", lambda g: g.node(TOPICS, "legacy", "bug bash", None)),
            ("win", "lose", lambda g: g.nodes[(TOPICS, "win")].update(mergedInto="x")),
            ("win", "lose", lambda g: g.nodes[(TOPICS, "lose")].update(mergedInto="elsewhere")),
        ],
        ids=["self", "missing", "other-org", "legacy", "winner-merged", "loser-merged-elsewhere"],
    )
    async def test_invalid_merges_are_refused(
        self, winner: str, loser: str, setup: Callable[[FakeGraph], object] | None,
    ) -> None:
        graph = _merge_fixture()
        if setup:
            setup(graph)
        with pytest.raises(ValueError):
            await _consolidator(graph).merge(TOPICS, ORG, winner, loser, dry_run=False)
        assert not [w for w in graph.writes if w.startswith(("move", "update"))]

    async def test_subcategory_merge_projects_the_level(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.node(SUB1, "w", "Policies", ORG, created=1)
        graph.node(SUB1, "l", "policies.", ORG, created=2)
        graph.link("r1", ORG, SUB1, "l")
        await _consolidator(graph, store).merge(SUB1, ORG, "w", "l", dry_run=False)
        (winner,) = [e for e in store.upserts if e.entity_id == "w"]
        assert (winner.entity_type, winner.level) == (EntityType.SUBCATEGORY, "1")


class TestUnmerge:
    async def test_unmerge_restores_the_graph(self) -> None:
        graph, store = _merge_fixture(), FakeStore()
        consolidator = _consolidator(graph, store)
        await consolidator.merge(TOPICS, ORG, "win", "lose", dry_run=False)
        restored = await consolidator.unmerge(TOPICS, ORG, "lose", dry_run=False)
        assert restored == 1  # r2's duplicate edge was dropped by the merge
        assert graph.targets("r1") == [(TOPICS, "lose")]
        assert next(e for e in graph.edges if e.record == "r1").merged_from is None
        assert "mergedInto" not in graph.nodes[(TOPICS, "lose")]
        assert {e.entity_id for e in store.upserts} >= {"lose", "win"}

    async def test_unmerge_leaves_the_winners_own_edges(self) -> None:
        graph = _merge_fixture()
        consolidator = _consolidator(graph)
        await consolidator.merge(TOPICS, ORG, "win", "lose", dry_run=False)
        await consolidator.unmerge(TOPICS, ORG, "lose", dry_run=False)
        assert graph.targets("r3") == [(TOPICS, "win")]

    async def test_unmerge_of_an_unmerged_node_is_refused(self) -> None:
        with pytest.raises(ValueError):
            await _consolidator(_merge_fixture()).unmerge(TOPICS, ORG, "lose", dry_run=False)


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


def _legacy_fixture() -> FakeGraph:
    graph = FakeGraph()
    graph.node(TOPICS, "L", "Pricing Strategy", None)
    graph.link("r1", ORG, TOPICS, "L", extracted="pricing strategy")
    graph.link("r2", ORG, TOPICS, "L")
    graph.link("x1", OTHER, TOPICS, "L")
    return graph


class TestLegacyMigration:
    async def test_lists_legacy_nodes_this_org_uses(self) -> None:
        nodes = await _consolidator(_legacy_fixture()).legacy_nodes(TOPICS, ORG)
        assert [(n.key, n.name, n.records) for n in nodes] == [("L", "Pricing Strategy", 2)]

    async def test_dry_run_writes_nothing(self) -> None:
        graph = _legacy_fixture()
        result = await _consolidator(graph).migrate_legacy(TOPICS, ORG, "L", dry_run=True)
        assert result.edges_moved == 2
        assert result.target_key == taxonomy_node_key(ORG, TOPICS, normalize_name("Pricing Strategy"))
        assert graph.writes == []

    async def test_moves_only_this_orgs_edges_onto_its_canonical_node(self) -> None:
        graph, store = _legacy_fixture(), FakeStore()
        result = await _consolidator(graph, store).migrate_legacy(TOPICS, ORG, "L", dry_run=False)
        target = result.target_key
        canonical = graph.nodes[(TOPICS, target)]
        assert canonical["orgId"] == ORG and canonical["normalizedName"] == "pricing strategy"
        assert graph.targets("r1") == [(TOPICS, target)]
        moved = next(e for e in graph.edges if e.record == "r1")
        assert (moved.migrated_from, moved.merged_from) == ("L", None)
        assert moved.extracted_name == "pricing strategy"
        assert graph.targets("x1") == [(TOPICS, "L")]  # the other org is untouched
        assert "mergedInto" not in graph.nodes[(TOPICS, "L")]  # still shared
        assert (ORG, "topic", ["L"]) in store.deletes
        assert any(e.entity_id == target for e in store.upserts)

    async def test_existing_canonical_node_is_reused(self) -> None:
        graph = _legacy_fixture()
        key = taxonomy_node_key(ORG, TOPICS, "pricing strategy")
        graph.node(TOPICS, key, "Pricing strategy", ORG)
        await _consolidator(graph).migrate_legacy(TOPICS, ORG, "L", dry_run=False)
        assert graph.nodes[(TOPICS, key)]["name"] == "Pricing strategy"
        assert graph.targets("r1") == [(TOPICS, key)]

    async def test_a_canonical_node_merged_away_redirects_to_its_winner(self) -> None:
        graph = _legacy_fixture()
        key = taxonomy_node_key(ORG, TOPICS, "pricing strategy")
        graph.node(TOPICS, key, "Pricing strategy", ORG, mergedInto="winner")
        graph.node(TOPICS, "winner", "Pricing", ORG)
        result = await _consolidator(graph).migrate_legacy(TOPICS, ORG, "L", dry_run=False)
        assert result.target_key == "winner"
        assert graph.targets("r1") == [(TOPICS, "winner")]

    async def test_unusable_name_is_skipped(self) -> None:
        graph = FakeGraph()
        graph.node(TOPICS, "L", "x", None)
        graph.link("r1", ORG, TOPICS, "L")
        result = await _consolidator(graph).migrate_legacy(TOPICS, ORG, "L", dry_run=False)
        assert result.skipped_reason and result.edges_moved == 0
        assert graph.writes == []

    async def test_an_org_node_is_not_legacy(self) -> None:
        graph = _legacy_fixture()
        graph.node(TOPICS, "mine", "Pricing", ORG)
        with pytest.raises(ValueError):
            await _consolidator(graph).migrate_legacy(TOPICS, ORG, "mine", dry_run=False)

    async def test_unmigrate_moves_the_org_back(self) -> None:
        graph = _legacy_fixture()
        consolidator = _consolidator(graph)
        result = await consolidator.migrate_legacy(TOPICS, ORG, "L", dry_run=False)
        restored = await consolidator.unmigrate_legacy(TOPICS, ORG, "L", result.target_key, dry_run=False)
        assert restored == 2
        assert graph.targets("r1") == [(TOPICS, "L")]
        assert next(e for e in graph.edges if e.record == "r1").merged_from is None


async def test_entity_store_is_optional() -> None:
    graph = _merge_fixture()
    result = await _consolidator(graph, None).merge(TOPICS, ORG, "win", "lose", dry_run=False)
    assert result.edges_moved == 2


async def test_store_failures_are_reported_not_raised() -> None:
    graph, store = _merge_fixture(), FakeStore()
    store.delete_entities = AsyncMock(side_effect=RuntimeError("vector db down"))
    result = await _consolidator(graph, store).merge(TOPICS, ORG, "win", "lose", dry_run=False)
    assert result.index_refreshed is False
    assert graph.nodes[(TOPICS, "lose")]["mergedInto"] == "win"


# ---------------------------------------------------------------------------
# Chained operations stay undoable
# ---------------------------------------------------------------------------


def _chain_fixture() -> FakeGraph:
    graph = FakeGraph()
    graph.node(TOPICS, "a", "bug-bash", ORG, created=3)
    graph.node(TOPICS, "b", "Bug bash", ORG, created=2)
    graph.node(TOPICS, "c", "Bugbash day", ORG, created=1)
    graph.link("ra", ORG, TOPICS, "a", extracted="bug-bash")
    graph.link("rb", ORG, TOPICS, "b")
    graph.link("rc", ORG, TOPICS, "c")
    return graph


class TestChains:
    async def test_merge_into_a_merged_chain_keeps_every_origin(self) -> None:
        graph = _chain_fixture()
        consolidator = _consolidator(graph)
        await consolidator.merge(TOPICS, ORG, "b", "a", dry_run=False)
        await consolidator.merge(TOPICS, ORG, "c", "b", dry_run=False)
        assert graph.targets("ra") == [(TOPICS, "c")]
        assert next(e for e in graph.edges if e.record == "ra").merged_from == "a"
        assert next(e for e in graph.edges if e.record == "rb").merged_from == "b"
        # a now redirects straight to c, never to a merged node.
        assert graph.nodes[(TOPICS, "a")]["mergedInto"] == "c"

    async def test_each_link_of_a_chain_undoes_on_its_own(self) -> None:
        graph = _chain_fixture()
        consolidator = _consolidator(graph)
        await consolidator.merge(TOPICS, ORG, "b", "a", dry_run=False)
        await consolidator.merge(TOPICS, ORG, "c", "b", dry_run=False)
        assert await consolidator.unmerge(TOPICS, ORG, "a", dry_run=False) == 1
        assert graph.targets("ra") == [(TOPICS, "a")]
        assert graph.targets("rb") == [(TOPICS, "c")]
        assert await consolidator.unmerge(TOPICS, ORG, "b", dry_run=False) == 1
        assert graph.targets("rb") == [(TOPICS, "b")]

    async def test_migration_survives_a_later_merge_and_still_undoes(self) -> None:
        graph = _legacy_fixture()
        consolidator = _consolidator(graph)
        result = await consolidator.migrate_legacy(TOPICS, ORG, "L", dry_run=False)
        graph.node(TOPICS, "older", "Pricing strategy!", ORG, created=0)
        await consolidator.merge(TOPICS, ORG, "older", result.target_key, dry_run=False)
        moved = next(e for e in graph.edges if e.record == "r1")
        assert (moved.migrated_from, moved.merged_from) == ("L", result.target_key)
        restored = await consolidator.unmigrate_legacy(TOPICS, ORG, "L", result.target_key, dry_run=False)
        assert restored == 2 and graph.targets("r1") == [(TOPICS, "L")]

    async def test_a_redirect_cycle_is_refused(self) -> None:
        graph = _chain_fixture()
        graph.nodes[(TOPICS, "a")]["mergedInto"] = "b"
        graph.nodes[(TOPICS, "b")]["mergedInto"] = "a"
        with pytest.raises(ValueError, match="cycle"):
            await _consolidator(graph).unmerge(TOPICS, ORG, "a", dry_run=False)

    async def test_merging_a_loser_merged_elsewhere_is_refused(self) -> None:
        graph = _chain_fixture()
        consolidator = _consolidator(graph)
        await consolidator.merge(TOPICS, ORG, "b", "a", dry_run=False)
        with pytest.raises(ValueError):
            await consolidator.merge(TOPICS, ORG, "c", "a", dry_run=False)


class TestMigrationFindsExistingVariants:
    async def test_an_alias_of_an_existing_node_is_used(self) -> None:
        graph = _legacy_fixture()
        graph.node(TOPICS, "pricing", "Pricing", ORG)
        graph.nodes[(TOPICS, "pricing")]["normalizedAliases"] = ["pricing strategy"]
        result = await _consolidator(graph).migrate_legacy(TOPICS, ORG, "L", dry_run=False)
        assert result.target_key == "pricing"
        assert not any(w.startswith("create:") for w in graph.writes)

    async def test_legacy_nodes_are_paged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.modules.entity_resolution import consolidation

        monkeypatch.setattr(consolidation, "_LEGACY_PAGE", 2)
        graph = FakeGraph()
        for i in range(5):
            graph.node(TOPICS, f"L{i}", f"Legacy {i}", None)
            graph.link(f"r{i}", ORG, TOPICS, f"L{i}")
        nodes = await _consolidator(graph).legacy_nodes(TOPICS, ORG)
        assert [n.key for n in nodes] == [f"L{i}" for i in range(5)]


class TestMigrationAndMergeHistoriesAreSeparate:
    """Migration provenance (migratedFrom) and merge provenance (mergedFrom)
    are separate fields, so undoing one never hides the other's edges."""

    async def test_migrate_merge_then_undo_both_returns_the_record_to_the_legacy_node(self) -> None:
        graph = _legacy_fixture()
        consolidator = _consolidator(graph)
        target = (await consolidator.migrate_legacy(TOPICS, ORG, "L", dry_run=False)).target_key
        graph.node(TOPICS, "w", "Pricing strategy!", ORG, created=0)
        await consolidator.merge(TOPICS, ORG, "w", target, dry_run=False)
        edge = next(e for e in graph.edges if e.record == "r1")
        assert (edge.merged_from, edge.migrated_from) == (target, "L")

        assert await consolidator.unmerge(TOPICS, ORG, target, dry_run=False) == 2
        assert graph.targets("r1") == [(TOPICS, target)]
        assert await consolidator.unmigrate_legacy(TOPICS, ORG, "L", target, dry_run=False) == 2
        assert graph.targets("r1") == [(TOPICS, "L")]
        edge = next(e for e in graph.edges if e.record == "r1")
        assert (edge.merged_from, edge.migrated_from) == (None, None)

    async def test_unmigrate_finds_edges_after_a_later_merge(self) -> None:
        graph = _legacy_fixture()
        consolidator = _consolidator(graph)
        target = (await consolidator.migrate_legacy(TOPICS, ORG, "L", dry_run=False)).target_key
        graph.node(TOPICS, "w", "Pricing strategy!", ORG, created=0)
        await consolidator.merge(TOPICS, ORG, "w", target, dry_run=False)
        assert await consolidator.unmigrate_legacy(TOPICS, ORG, "L", target, dry_run=False) == 2
        assert graph.targets("r1") == [(TOPICS, "L")]

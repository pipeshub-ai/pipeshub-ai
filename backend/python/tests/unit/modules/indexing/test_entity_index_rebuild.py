"""The entity index rebuild: projects the graph into the entities collection,
one page per tick, resumable, leader-only, and sweeps stale points.

Fakes stand in for the graph provider, the entity store and the leader lock,
so each test pins one rule of the loop. The provider queries themselves are
covered in ``tests/unit/services/graph_db/test_entity_index_queries.py`` and
against real servers in ``tests/integration/graph_db``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityType
from app.modules.indexing import entity_index_rebuild as mod
from app.modules.indexing.entity_index_rebuild import (
    ENTITY_INDEX_TAXONOMY_SOURCES,
    ENTITY_INDEX_VERSION,
    MAX_ATTEMPTS,
    MAX_TICK_ERRORS,
    TAXONOMY_PAGE_SIZE,
    EntityIndexRebuilder,
    EntityIndexState,
    entity_index_marker,
    fingerprint_of,
)
from app.modules.transformers.entity_vectorstore import (
    EntityPointRef,
    EntityWriteOutcome,
)

APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value
RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value
DEPARTMENTS = CollectionNames.DEPARTMENTS.value
SUB2 = CollectionNames.SUBCATEGORIES2.value

FP = "openai:text-embedding-3-small:1536"
MARKER = entity_index_marker(FP)
NOW = 1_800_000_000_000


class FakeGraph:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict[str, Any]]] = {APPS: {}, ORGS: {}}
        self.sources: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.membership: dict[tuple[str, str], dict[str, list[str]]] = {}
        self.nodes: dict[str, dict[str, dict[str, Any]]] = {}
        self.membership_error: Exception | None = None
        self.lookup_error: Exception | None = None
        self.page_calls: list[tuple[str, str, str | None, int]] = []
        self.updates: list[tuple[str, str, dict[str, Any]]] = []

    async def get_entity_index_candidate(
        self, collection: str, marker: str, *, sweep_before: int | None = None,
        transaction: str | None = None,
    ) -> dict | None:
        for key in sorted(self.docs[collection]):
            doc = self.docs[collection][key]
            if collection == APPS and doc.get("status") == "DELETING":
                continue
            if doc.get(EntityIndexState.STATE) != marker:
                return dict(doc)
            if sweep_before is not None:
                swept = doc.get(EntityIndexState.SWEPT_AT)
                if swept is None or swept < sweep_before:
                    return dict(doc)
        return None

    async def page_entity_index_source(
        self, source: str, scope_id: str, after_key: str | None, limit: int,
        transaction: str | None = None,
    ) -> list[dict]:
        self.page_calls.append((source, scope_id, after_key, limit))
        rows = sorted(self.sources.get((source, scope_id), []), key=lambda r: r["_key"])
        if after_key:
            rows = [r for r in rows if r["_key"] > after_key]
        return [dict(r) for r in rows[:limit]]

    async def update_node(self, key: str, collection: str, updates: dict[str, Any]) -> bool:
        self.updates.append((key, collection, dict(updates)))
        self.docs[collection][key].update(updates)
        return True

    async def get_taxonomy_entity_membership(
        self, refs: list[dict[str, Any]], org_id: str, transaction: str | None = None,
    ) -> dict[tuple[str, str], dict[str, list[str]]]:
        if self.membership_error:
            raise self.membership_error
        return {
            (r["type"], r["id"]): self.membership.get(
                (r["type"], r["id"]), {"connectorIds": [], "recordGroupIds": []},
            )
            for r in refs
        }

    async def get_nodes_by_field_in(
        self, collection: str, field_name: str, field_values: list[Any],
        return_fields: list[str] | None = None, transaction: str | None = None,
        *, raise_on_error: bool = False,
    ) -> list[dict]:
        if self.lookup_error:
            if raise_on_error:
                raise self.lookup_error
            return []
        nodes = self.nodes.get(collection, {})
        return [dict(nodes[v], id=v) for v in field_values if v in nodes]


class FakeStore:
    def __init__(self, fingerprint: str = FP) -> None:
        self.fingerprint = fingerprint
        self.upserts: list[dict[str, Any]] = []
        self.deletes: list[tuple[str, str, list[str]]] = []
        self.fail_ids: set[str] = set()
        self.points: list[EntityPointRef] = []
        self.page_calls: list[tuple[str, list[str], str | None, int]] = []

    async def embedding_fingerprint(self) -> str:
        return self.fingerprint

    async def upsert_entities_batch(
        self, entities: list, batch_size: int = 64, *, merge_membership: bool = True,
    ) -> EntityWriteOutcome:
        self.upserts.append({"entities": list(entities), "merge": merge_membership})
        failed = sum(1 for e in entities if e.entity_id in self.fail_ids)
        return EntityWriteOutcome(written=len(entities) - failed, failed=failed)

    async def delete_entities(self, org_id: str, entity_type: str, entity_ids: list[str]) -> None:
        self.deletes.append((org_id, entity_type, sorted(entity_ids)))

    async def page_entity_points(
        self, org_id: str, entity_types: list[str], *, offset: str | None = None,
        limit: int = 500,
    ) -> tuple[list[EntityPointRef], str | None]:
        self.page_calls.append((org_id, list(entity_types), offset, limit))
        start = int(offset or 0)
        page = self.points[start:start + limit]
        nxt = start + limit
        return page, (str(nxt) if nxt < len(self.points) else None)

    def offset_after_delete(self, next_offset: str | None, deleted: int) -> str | None:
        return next_offset

    def written(self) -> list:
        return [e for call in self.upserts for e in call["entities"]]


class PositionalStore(FakeStore):
    """Pages by result position and really deletes, as Redis does."""

    async def delete_entities(self, org_id: str, entity_type: str, entity_ids: list[str]) -> None:
        await super().delete_entities(org_id, entity_type, entity_ids)
        gone = set(entity_ids)
        self.points = [
            p for p in self.points if not (p.entity_type == entity_type and p.entity_id in gone)
        ]

    def offset_after_delete(self, next_offset: str | None, deleted: int) -> str | None:
        return None if next_offset is None else str(max(0, int(next_offset) - deleted))


class FakeLock:
    def __init__(self, leader: bool = True, keep: bool = True) -> None:
        self.leader = leader
        self.keep = keep

    async def try_acquire(self) -> bool:
        return self.leader

    async def refresh(self) -> bool:
        return self.keep

    async def release(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _rebuilder(
    graph: FakeGraph, store: FakeStore, lock: FakeLock | None = None, *,
    page_size: int = 2, sweep_points_per_tick: int = 100,
) -> EntityIndexRebuilder:
    return EntityIndexRebuilder(
        logger=logging.getLogger("entity-index-test"),
        graph_provider=graph,
        store=store,
        lock=lock or FakeLock(),
        page_size=page_size,
        sweep_points_per_tick=sweep_points_per_tick,
        now_ms=lambda: NOW,
    )


def _app(key: str = "app-1", /, **extra: object) -> dict[str, Any]:
    return {"_key": key, "name": key, **extra}


def _org(key: str = "org-1", /, **extra: object) -> dict[str, Any]:
    return {"_key": key, **extra}


def _done_org(key: str = "org-1", /, **extra: object) -> dict[str, Any]:
    return _org(key, **{EntityIndexState.STATE: MARKER, EntityIndexState.SWEPT_AT: NOW, **extra})


def _rec(key: str, *, name: str = "", org: str = "org-1", group: str | None = "g1",
         status: str = "COMPLETED", deleted: bool = False) -> dict[str, Any]:
    return {
        "_key": key, "name": name or f"doc {key}", "orgId": org, "recordGroupId": group,
        "indexingStatus": status, "isDeleted": deleted,
    }


async def _run_until_idle(rebuilder: EntityIndexRebuilder, limit: int = 50) -> list[str]:
    outcomes = []
    for _ in range(limit):
        outcome = await rebuilder.tick()
        outcomes.append(outcome)
        if outcome == "idle":
            return outcomes
    raise AssertionError(f"never went idle: {outcomes}")


# ---------------------------------------------------------------------------
# Marker
# ---------------------------------------------------------------------------


class TestMarker:
    def test_marker_carries_version_and_fingerprint(self) -> None:
        assert MARKER == f"v{ENTITY_INDEX_VERSION}:{FP}"
        assert fingerprint_of(MARKER) == FP

    def test_marker_changes_with_the_fingerprint(self) -> None:
        assert entity_index_marker("a:b:3") != entity_index_marker("a:b:4")

    @pytest.mark.parametrize("value", [None, "", "garbage", 7])
    def test_unreadable_marker_has_no_fingerprint(self, value: object) -> None:
        assert fingerprint_of(value) is None


# ---------------------------------------------------------------------------
# Leadership and ordering
# ---------------------------------------------------------------------------


class TestLeadership:
    async def test_follower_does_nothing(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        assert await _rebuilder(graph, store, FakeLock(leader=False)).tick() == "not_leader"
        assert graph.page_calls == [] and store.upserts == [] and graph.updates == []

    async def test_nothing_to_do_is_idle(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _done_org()
        assert await _rebuilder(graph, store).tick() == "idle"

    async def test_connectors_go_before_orgs(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.docs[ORGS]["org-1"] = _org()
        assert await _rebuilder(graph, store).tick() == "connector"

    async def test_lost_leadership_writes_no_state(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(GROUPS, "app-1")] = [{"_key": "g1", "name": "Eng", "orgId": "org-1"}]
        await _rebuilder(graph, store, FakeLock(keep=False)).tick()
        assert graph.updates == []


# ---------------------------------------------------------------------------
# Connector pass
# ---------------------------------------------------------------------------


class TestConnectorPass:
    async def test_full_pass_pages_groups_then_records_and_completes(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.docs[ORGS]["org-1"] = _done_org()
        graph.sources[(GROUPS, "app-1")] = [
            {"_key": "g1", "name": "Eng", "orgId": "org-1"},
            {"_key": "g2", "name": "Ops", "orgId": "org-1"},
        ]
        graph.sources[(RECORDS, "app-1")] = [_rec("r1"), _rec("r2"), _rec("r3")]
        outcomes = await _run_until_idle(_rebuilder(graph, store))

        assert outcomes.count("connector") == 4
        assert [c[:3] for c in graph.page_calls] == [
            (GROUPS, "app-1", None), (GROUPS, "app-1", "g2"),
            (RECORDS, "app-1", None), (RECORDS, "app-1", "r2"),
        ]
        app = graph.docs[APPS]["app-1"]
        assert app[EntityIndexState.STATE] == MARKER
        assert app[EntityIndexState.PHASE] is None
        assert app[EntityIndexState.AFTER_KEY] is None
        assert app[EntityIndexState.ATTEMPTS] == 0
        assert app[EntityIndexState.EXHAUSTED] is False

    async def test_points_are_written_in_replace_mode_with_own_membership(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(GROUPS, "app-1")] = [{"_key": "g1", "name": "Eng", "orgId": "org-1"}]
        graph.sources[(RECORDS, "app-1")] = [_rec("r1", name="Q3 plan", group="g1")]
        await _run_until_idle(_rebuilder(graph, store))

        assert all(call["merge"] is False for call in store.upserts)
        by_type = {e.entity_type: e for e in store.written()}
        group = by_type[EntityType.RECORD_GROUP]
        assert (group.entity_id, group.name, group.org_id) == ("g1", "Eng", "org-1")
        assert group.connector_ids == ["app-1"] and group.record_group_ids == ["g1"]
        record = by_type[EntityType.RECORD]
        assert (record.entity_id, record.name) == ("r1", "Q3 plan")
        assert record.connector_ids == ["app-1"] and record.record_group_ids == ["g1"]

    async def test_record_without_group_has_no_group_membership(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [_rec("r1", group=None)]
        await _run_until_idle(_rebuilder(graph, store))
        assert store.written()[0].record_group_ids == []

    async def test_unindexed_deleted_unnamed_and_orgless_rows_are_skipped(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [
            _rec("r1", status="QUEUED"),
            _rec("r2", deleted=True),
            {**_rec("r3"), "name": "   "},
            _rec("r4", org=""),
            _rec("r5"),
        ]
        graph.sources[(GROUPS, "app-1")] = [
            {"_key": "g0", "name": "", "orgId": "org-1"},
            {"_key": "g1", "name": "Eng", "orgId": None},
        ]
        await _run_until_idle(_rebuilder(graph, store, page_size=10))
        assert [e.entity_id for e in store.written()] == ["r5"]
        assert graph.docs[APPS]["app-1"][EntityIndexState.STATE] == MARKER

    async def test_resumes_from_the_saved_phase_and_cursor(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app(**{
            EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: RECORDS, EntityIndexState.AFTER_KEY: "r2",
        })
        graph.sources[(RECORDS, "app-1")] = [_rec("r1"), _rec("r2"), _rec("r3")]
        await _rebuilder(graph, store).tick()
        assert graph.page_calls[0][:3] == (RECORDS, "app-1", "r2")
        assert [e.entity_id for e in store.written()] == ["r3"]

    async def test_unknown_saved_phase_restarts_the_pass(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app(**{
            EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: "nonsense", EntityIndexState.AFTER_KEY: "zz",
        })
        await _rebuilder(graph, store).tick()
        assert graph.page_calls[0][:3] == (GROUPS, "app-1", None)

    async def test_write_failures_retry_the_pass_then_give_up(self, caplog) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [_rec("r1"), _rec("r2")]
        store.fail_ids = {"r2"}
        rebuilder = _rebuilder(graph, store, page_size=10)

        for attempt in range(1, MAX_ATTEMPTS):
            await rebuilder.tick()  # groups (empty)
            await rebuilder.tick()  # records: completes with a failure
            app = graph.docs[APPS]["app-1"]
            assert app[EntityIndexState.ATTEMPTS] == attempt
            assert app.get(EntityIndexState.STATE) != MARKER
            assert app[EntityIndexState.FAILURES] == 0
            assert app[EntityIndexState.PHASE] is None

        with caplog.at_level(logging.ERROR, logger="entity-index-test"):
            await rebuilder.tick()
            await rebuilder.tick()
        app = graph.docs[APPS]["app-1"]
        assert app[EntityIndexState.STATE] == MARKER
        assert app[EntityIndexState.EXHAUSTED] is True
        assert app[EntityIndexState.FAILURES] == 1
        assert any("app-1" in r.getMessage() for r in caplog.records)

    async def test_failures_on_an_earlier_page_are_carried_to_completion(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [_rec("r1"), _rec("r2"), _rec("r3")]
        store.fail_ids = {"r1"}
        rebuilder = _rebuilder(graph, store)
        await rebuilder.tick()  # groups
        await rebuilder.tick()  # r1, r2: full page with one failure
        assert graph.docs[APPS]["app-1"][EntityIndexState.FAILURES] == 1
        await rebuilder.tick()  # r3: completes
        assert graph.docs[APPS]["app-1"][EntityIndexState.ATTEMPTS] == 1

    async def test_page_query_error_is_charged_to_the_document(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.page_entity_index_source = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(RuntimeError):
            await _rebuilder(graph, store).tick()
        app = graph.docs[APPS]["app-1"]
        assert app[EntityIndexState.ERRORS] == 1
        assert app.get(EntityIndexState.STATE) != MARKER

    async def test_a_document_that_always_errors_is_given_up(self, caplog) -> None:
        """Otherwise it stays first in key order and starves every other app and org."""
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.docs[APPS]["app-2"] = _app("app-2")
        real_page = graph.page_entity_index_source

        async def _page(source: str, scope_id: str, *args: object, **kwargs: object) -> list[dict]:
            if scope_id == "app-1":
                raise RuntimeError("poison")
            return await real_page(source, scope_id, *args, **kwargs)

        graph.page_entity_index_source = _page
        rebuilder = _rebuilder(graph, store)
        with caplog.at_level(logging.ERROR, logger="entity-index-test"):
            for _ in range(MAX_TICK_ERRORS):
                with pytest.raises(RuntimeError):
                    await rebuilder.tick()
        app = graph.docs[APPS]["app-1"]
        assert app[EntityIndexState.STATE] == MARKER and app[EntityIndexState.EXHAUSTED] is True
        assert any("consecutive errors" in r.getMessage() for r in caplog.records)
        assert await rebuilder.tick() == "connector"
        assert graph.page_calls[-1][1] == "app-2"

    async def test_a_clean_page_resets_the_error_count(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app(**{
            EntityIndexState.TARGET: MARKER, EntityIndexState.ERRORS: 4,
        })
        await _rebuilder(graph, store).tick()
        assert graph.docs[APPS]["app-1"][EntityIndexState.ERRORS] == 0


class TestMarkerChangeMidPass:
    """Progress is only valid for the marker it was built for (a model change
    mid-pass, e.g. on the first long pass after upgrade)."""

    async def test_cursor_from_another_marker_restarts_the_pass(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app(**{
            EntityIndexState.TARGET: entity_index_marker("old:model:768"),
            EntityIndexState.PHASE: RECORDS, EntityIndexState.AFTER_KEY: "r2",
            EntityIndexState.FAILURES: 5,
        })
        graph.sources[(GROUPS, "app-1")] = [{"_key": "g1", "name": "Eng", "orgId": "org-1"}]
        graph.sources[(RECORDS, "app-1")] = [_rec("r1"), _rec("r2"), _rec("r3")]
        await _rebuilder(graph, store, page_size=10).tick()
        assert graph.page_calls[0][:3] == (GROUPS, "app-1", None)
        app = graph.docs[APPS]["app-1"]
        assert app[EntityIndexState.TARGET] == MARKER
        assert app[EntityIndexState.PHASE] == RECORDS and app[EntityIndexState.AFTER_KEY] is None
        assert app[EntityIndexState.FAILURES] == 0

    async def test_exhausted_document_gets_full_attempts_under_a_new_marker(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        old = entity_index_marker("old:model:768")
        graph.docs[APPS]["app-1"] = _app(**{
            EntityIndexState.STATE: old, EntityIndexState.TARGET: old,
            EntityIndexState.ATTEMPTS: MAX_ATTEMPTS, EntityIndexState.FAILURES: 7,
            EntityIndexState.EXHAUSTED: True,
        })
        graph.sources[(RECORDS, "app-1")] = [_rec("r1")]
        store.fail_ids = {"r1"}
        rebuilder = _rebuilder(graph, store)
        await rebuilder.tick()
        await rebuilder.tick()
        app = graph.docs[APPS]["app-1"]
        assert app[EntityIndexState.ATTEMPTS] == 1
        assert app.get(EntityIndexState.STATE) == old
        assert app[EntityIndexState.EXHAUSTED] is False


# ---------------------------------------------------------------------------
# Org taxonomy pass
# ---------------------------------------------------------------------------


class TestTaxonomyPass:
    def test_sources_cover_every_taxonomy_collection_and_departments(self) -> None:
        assert set(ENTITY_INDEX_TAXONOMY_SOURCES) == {
            CATEGORIES, DEPARTMENTS, CollectionNames.LANGUAGES.value,
            CollectionNames.SUBCATEGORIES1.value, SUB2,
            CollectionNames.SUBCATEGORIES3.value, TOPICS,
        }

    async def test_walks_each_collection_once_and_completes(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.SWEPT_AT: NOW})
        outcomes = await _run_until_idle(_rebuilder(graph, store))
        assert outcomes.count("taxonomy") == len(ENTITY_INDEX_TAXONOMY_SOURCES)
        assert [c[0] for c in graph.page_calls] == list(ENTITY_INDEX_TAXONOMY_SOURCES)
        assert all(c[1] == "org-1" for c in graph.page_calls)
        assert graph.docs[ORGS]["org-1"][EntityIndexState.STATE] == MARKER

    async def test_reached_nodes_get_graph_membership_and_unreached_are_deleted(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{
            EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: TOPICS, EntityIndexState.SWEPT_AT: NOW,
        })
        graph.sources[(TOPICS, "org-1")] = [
            {"_key": "t1", "name": "Pricing", "aliases": ["pricing model"]},
            {"_key": "t2", "name": "Orphan", "aliases": []},
        ]
        graph.membership[("topic", "t1")] = {"connectorIds": ["c1", "c2"], "recordGroupIds": ["g1"]}
        await _rebuilder(graph, store, page_size=10).tick()

        (written,) = store.written()
        assert (written.entity_id, written.entity_type, written.name) == ("t1", EntityType.TOPIC, "Pricing")
        assert written.aliases == ["pricing model"]
        assert written.connector_ids == ["c1", "c2"] and written.record_group_ids == ["g1"]
        assert written.org_id == "org-1"
        assert store.upserts[0]["merge"] is False
        assert store.deletes == [("org-1", "topic", ["t2"])]

    async def test_subcategory_level_comes_from_its_collection(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: SUB2})
        graph.sources[(SUB2, "org-1")] = [{"_key": "s1", "name": "Pricing", "aliases": []}]
        graph.membership[("subcategory", "s1")] = {"connectorIds": ["c1"], "recordGroupIds": []}
        await _rebuilder(graph, store).tick()
        (written,) = store.written()
        assert (written.entity_type, written.level) == (EntityType.SUBCATEGORY, "2")

    async def test_departments_are_projected_by_name(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: DEPARTMENTS})
        graph.sources[(DEPARTMENTS, "org-1")] = [{"_key": "d1", "name": "Finance"}]
        graph.membership[("department", "d1")] = {"connectorIds": ["c1"], "recordGroupIds": []}
        await _rebuilder(graph, store).tick()
        (written,) = store.written()
        assert (written.entity_type, written.name) == (EntityType.DEPARTMENT, "Finance")

    async def test_membership_lookup_failure_counts_the_page_and_deletes_nothing(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: TOPICS})
        graph.sources[(TOPICS, "org-1")] = [{"_key": "t1", "name": "A"}, {"_key": "t2", "name": "B"}]
        graph.membership_error = RuntimeError("db down")
        await _rebuilder(graph, store).tick()
        assert store.upserts == [] and store.deletes == []
        org = graph.docs[ORGS]["org-1"]
        assert org[EntityIndexState.FAILURES] == 2
        assert org[EntityIndexState.AFTER_KEY] == "t2"

    async def test_unnamed_node_is_skipped(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: TOPICS})
        graph.sources[(TOPICS, "org-1")] = [{"_key": "t1", "name": " "}]
        await _rebuilder(graph, store).tick()
        assert store.upserts == [] and store.deletes == []


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def _point(entity_type: str, entity_id: str, level: str | None = None) -> EntityPointRef:
    return EntityPointRef(entity_type=entity_type, entity_id=entity_id, level=level)


class TestSweep:
    async def test_runs_after_the_pass_and_deletes_only_stale_points(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        graph.nodes = {
            TOPICS: {"t-live": {"orgId": "org-1"}, "t-legacy": {"orgId": None},
                     "t-other": {"orgId": "org-2"}},
            DEPARTMENTS: {"d-global": {"orgId": None}, "d-mine": {"orgId": "org-1"}},
            GROUPS: {"g-live": {"orgId": "org-1"}, "g-other": {"orgId": "org-2"}},
            SUB2: {"s-live": {"orgId": "org-1"}},
        }
        store.points = [
            _point("topic", "t-live"), _point("topic", "t-legacy"), _point("topic", "t-other"),
            _point("topic", "t-gone"),
            _point("department", "d-global"), _point("department", "d-mine"),
            _point("department", "d-gone"),
            _point("record_group", "g-live"), _point("record_group", "g-other"),
            _point("record_group", "g-gone"),
            _point("subcategory", "s-live", "2"), _point("subcategory", "s-gone", "2"),
            _point("subcategory", "s-nolevel", None),
        ]
        assert await _rebuilder(graph, store).tick() == "sweep"

        assert sorted(store.deletes) == [
            ("org-1", "department", ["d-gone"]),
            ("org-1", "record_group", ["g-gone", "g-other"]),
            ("org-1", "subcategory", ["s-gone"]),
            ("org-1", "topic", ["t-gone", "t-other"]),
        ]
        org = graph.docs[ORGS]["org-1"]
        assert org[EntityIndexState.SWEPT_AT] == NOW
        assert org[EntityIndexState.SWEEP_OFFSET] is None

    async def test_record_points_are_never_swept(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        await _rebuilder(graph, store).tick()
        (call,) = store.page_calls
        assert "record" not in call[1]
        assert {"topic", "category", "subcategory", "language", "department", "record_group"} <= set(call[1])

    async def test_sweep_is_resumable_across_ticks(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        store.points = [_point("topic", f"t{i}") for i in range(5)]
        rebuilder = _rebuilder(graph, store, sweep_points_per_tick=2)
        await rebuilder.tick()
        org = graph.docs[ORGS]["org-1"]
        assert org[EntityIndexState.SWEEP_OFFSET] == "2"
        assert org.get(EntityIndexState.SWEPT_AT) is None
        await rebuilder.tick()
        await rebuilder.tick()
        assert [c[2] for c in store.page_calls] == [None, "2", "4"]
        assert org[EntityIndexState.SWEPT_AT] == NOW
        assert sorted(i for d in store.deletes for i in d[2]) == [f"t{i}" for i in range(5)]

    async def test_deletes_do_not_make_a_positional_sweep_skip_points(self) -> None:
        graph, store = FakeGraph(), PositionalStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        graph.nodes[TOPICS] = {"t1": {"orgId": "org-1"}, "t4": {"orgId": "org-1"}}
        store.points = [_point("topic", f"t{i}") for i in range(6)]
        rebuilder = _rebuilder(graph, store, sweep_points_per_tick=2)
        for _ in range(4):
            await rebuilder.tick()
        assert graph.docs[ORGS]["org-1"][EntityIndexState.SWEPT_AT] == NOW
        assert sorted(p.entity_id for p in store.points) == ["t1", "t4"]

    async def test_lookup_failure_deletes_nothing_then_gives_up(self, caplog) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        store.points = [_point("topic", "t1")]
        graph.lookup_error = RuntimeError("db down")
        rebuilder = _rebuilder(graph, store)
        for attempt in range(1, MAX_ATTEMPTS):
            with pytest.raises(RuntimeError):
                await rebuilder.tick()
            assert graph.docs[ORGS]["org-1"][EntityIndexState.SWEEP_FAILURES] == attempt
        with caplog.at_level(logging.ERROR, logger="entity-index-test"), pytest.raises(RuntimeError):
            await rebuilder.tick()
        assert store.deletes == []
        org = graph.docs[ORGS]["org-1"]
        assert org[EntityIndexState.SWEPT_AT] == NOW
        assert org[EntityIndexState.SWEEP_FAILURES] == 0
        assert any("org-1" in r.getMessage() for r in caplog.records)

    async def test_lost_leadership_deletes_nothing(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        store.points = [_point("topic", "t-gone")]
        await _rebuilder(graph, store, FakeLock(keep=False)).tick()
        assert store.deletes == [] and graph.updates == []

    async def test_recent_sweep_is_not_repeated(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _done_org()
        assert await _rebuilder(graph, store).tick() == "idle"
        assert store.page_calls == []


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


class TestLoop:
    async def test_tick_failure_backs_off_and_recovers(self) -> None:
        sleeps: list[float] = []

        async def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 4:
                raise asyncio.CancelledError

        outcomes = iter([RuntimeError("db down"), "idle", "connector"])

        async def _tick(self: EntityIndexRebuilder) -> str:
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("entity-index-test"))
        container.entity_vector_store = AsyncMock(return_value=FakeStore())
        with patch.object(mod.asyncio, "sleep", _sleep), \
             patch.object(EntityIndexRebuilder, "tick", _tick), \
             patch.object(mod.MessagingUtils, "_get_redis_config", AsyncMock(return_value=MagicMock())):
            with pytest.raises(asyncio.CancelledError):
                await mod.run_entity_index_rebuild_loop(container, FakeGraph())

        grace, failed, idle, busy = sleeps
        assert grace == mod.STARTUP_GRACE_SECONDS
        assert failed == mod.IDLE_INTERVAL_SECONDS * mod._BACKOFF_FACTOR
        assert idle == mod.IDLE_INTERVAL_SECONDS
        assert busy == mod.BUSY_INTERVAL_SECONDS
        assert busy < idle

    async def test_store_unavailable_skips_the_tick(self) -> None:
        sleeps: list[float] = []

        async def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise asyncio.CancelledError

        tick = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("entity-index-test"))
        container.entity_vector_store = AsyncMock(side_effect=RuntimeError("no store"))
        with patch.object(mod.asyncio, "sleep", _sleep), \
             patch.object(EntityIndexRebuilder, "tick", tick), \
             patch.object(mod.MessagingUtils, "_get_redis_config", AsyncMock(return_value=MagicMock())):
            with pytest.raises(asyncio.CancelledError):
                await mod.run_entity_index_rebuild_loop(container, FakeGraph())
        tick.assert_not_awaited()


class TestSameShapeAsIndexTime:
    """A projected point must equal what indexing writes, or the rebuild
    re-embeds every unchanged point instead of skipping it."""

    async def test_record_and_group_points_match_the_index_path_builders(self) -> None:
        from app.models.entities import EntityRecord

        graph, store = FakeGraph(), FakeStore()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(GROUPS, "app-1")] = [{"_key": "g1", "name": "Eng ", "orgId": "org-1"}]
        graph.sources[(RECORDS, "app-1")] = [_rec("r1", name=" Q3 plan", group="g1")]
        await _run_until_idle(_rebuilder(graph, store))
        group, record = store.written()
        assert group == EntityRecord.for_record_group("g1", "Eng ", "org-1", "app-1")
        assert record == EntityRecord.for_record("r1", " Q3 plan", "org-1", "app-1", "g1")


class TestTaxonomyRaces:
    async def test_lost_lease_during_a_slow_membership_read_writes_nothing(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: TOPICS})
        graph.sources[(TOPICS, "org-1")] = [{"_key": "t1", "name": "A"}, {"_key": "t2", "name": "B"}]
        graph.membership[("topic", "t1")] = {"connectorIds": ["c1"], "recordGroupIds": []}
        assert await _rebuilder(graph, store, FakeLock(keep=False)).tick() == "taxonomy"
        assert store.upserts == [] and store.deletes == [] and graph.updates == []

    async def test_node_linked_between_reads_is_not_deleted(self) -> None:
        """Indexing wrote the point after the first read; deleting it would
        hide the entity until another of its records is indexed."""
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.TARGET: MARKER, EntityIndexState.PHASE: TOPICS})
        graph.sources[(TOPICS, "org-1")] = [{"_key": "t1", "name": "A"}, {"_key": "t2", "name": "B"}]
        reads = {"n": 0}
        real = graph.get_taxonomy_entity_membership

        async def _membership(refs: list[dict], org_id: str, transaction: str | None = None) -> dict:
            reads["n"] += 1
            if reads["n"] == 2:
                graph.membership[("topic", "t1")] = {"connectorIds": ["c9"], "recordGroupIds": []}
            return await real(refs, org_id)

        graph.get_taxonomy_entity_membership = _membership
        await _rebuilder(graph, store).tick()
        assert store.deletes == [("org-1", "topic", ["t2"])]

    async def test_taxonomy_pages_are_capped(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org()
        await _rebuilder(graph, store, page_size=1000).tick()
        assert graph.page_calls[0][3] == TAXONOMY_PAGE_SIZE


class TestSweepScanFailures:
    async def test_scroll_failure_is_counted_and_the_sweep_eventually_moves_on(self) -> None:
        """Redis refuses offsets past 10,000; an uncounted failure would keep
        this org first in line for ever."""
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        graph.docs[ORGS]["org-2"] = _org("org-2", **{EntityIndexState.STATE: MARKER})
        store.page_entity_points = AsyncMock(side_effect=RuntimeError("offset beyond 10000"))
        rebuilder = _rebuilder(graph, store)
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(RuntimeError):
                await rebuilder.tick()
        assert graph.docs[ORGS]["org-1"][EntityIndexState.SWEPT_AT] == NOW
        store.page_entity_points = AsyncMock(return_value=([], None))
        assert await rebuilder.tick() == "sweep"
        assert graph.docs[ORGS]["org-2"][EntityIndexState.SWEPT_AT] == NOW

    async def test_a_delete_failure_is_counted_and_the_sweep_eventually_moves_on(self) -> None:
        """A delete one backend always rejects must not keep this org first
        in line for ever, as a scan failure must not."""
        graph, store = FakeGraph(), FakeStore()
        graph.docs[ORGS]["org-1"] = _org(**{EntityIndexState.STATE: MARKER})
        graph.docs[ORGS]["org-2"] = _org("org-2", **{EntityIndexState.STATE: MARKER})
        store.page_entity_points = AsyncMock(return_value=([_point("topic", "t-gone")], None))
        store.delete_entities = AsyncMock(side_effect=RuntimeError("delete-by-query rejected"))
        rebuilder = _rebuilder(graph, store)
        for attempt in range(1, MAX_ATTEMPTS):
            with pytest.raises(RuntimeError):
                await rebuilder.tick()
            assert graph.docs[ORGS]["org-1"][EntityIndexState.SWEEP_FAILURES] == attempt
        with pytest.raises(RuntimeError):
            await rebuilder.tick()
        assert graph.docs[ORGS]["org-1"][EntityIndexState.SWEPT_AT] == NOW
        store.page_entity_points = AsyncMock(return_value=([], None))
        assert await rebuilder.tick() == "sweep"
        assert graph.docs[ORGS]["org-2"][EntityIndexState.SWEPT_AT] == NOW

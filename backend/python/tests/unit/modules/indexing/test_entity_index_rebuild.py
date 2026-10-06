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
    EMPTY_CONFIRM_MS,
    ENTITY_INDEX_TAXONOMY_SOURCES,
    ENTITY_INDEX_VERSION,
    MAX_ATTEMPTS,
    MAX_TICK_ERRORS,
    REFILL_STATE_KEY,
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
        self.version_reads = 0
        self.recreate_requests: list[bool] = []
        # What the collection reports holding; a populated index by default.
        self.count = 1
        self.count_reads = 0
        self.ensured = 0

    async def embedding_fingerprint(self, *, recreate: bool = False) -> str:
        self.recreate_requests.append(recreate)
        return self.fingerprint

    async def points_count(self) -> int:
        self.count_reads += 1
        return self.count

    async def ensure_collection(self) -> None:
        self.ensured += 1

    async def embedding_config_version(self) -> str | None:
        self.version_reads += 1
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


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> int:
        return self.now


def _rebuilder(
    graph: FakeGraph, store: FakeStore, lock: FakeLock | None = None, *,
    page_size: int = 2, sweep_points_per_tick: int = 100, config_service: object = None,
    now_ms: Clock | None = None,
) -> EntityIndexRebuilder:
    return EntityIndexRebuilder(
        logger=logging.getLogger("entity-index-test"),
        graph_provider=graph,
        store=store,
        lock=lock or FakeLock(),
        page_size=page_size,
        sweep_points_per_tick=sweep_points_per_tick,
        config_service=config_service,
        now_ms=now_ms or (lambda: NOW),
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


async def _run_until_settled(rebuilder: EntityIndexRebuilder, clock: Clock, limit: int = 60) -> list[str]:
    """Tick as the loop does, an idle interval passing after each idle tick,
    until two idle ticks in a row: an empty index is refilled on the second."""
    outcomes: list[str] = []
    for _ in range(limit):
        outcome = await rebuilder.tick()
        outcomes.append(outcome)
        if outcome == "idle":
            if outcomes[-2:-1] == ["idle"]:
                return outcomes
            clock.now += int(mod.IDLE_INTERVAL_SECONDS * 1000)
    raise AssertionError(f"never settled: {outcomes}")


# ---------------------------------------------------------------------------
# Marker
# ---------------------------------------------------------------------------


class TestMarker:
    def test_marker_carries_version_and_fingerprint(self) -> None:
        assert MARKER == f"v{ENTITY_INDEX_VERSION}:{FP}"
        assert fingerprint_of(MARKER) == FP

    def test_marker_changes_with_the_fingerprint(self) -> None:
        assert entity_index_marker("a:b:3") != entity_index_marker("a:b:4")

    @pytest.mark.parametrize("value", [None, "", "garbage", 7, "v:x", "vr2:x", "v1r:x", "v1:"])
    def test_unreadable_marker_has_no_fingerprint(self, value: object) -> None:
        assert fingerprint_of(value) is None

    def test_a_refill_gives_a_marker_no_document_has_reached(self) -> None:
        assert entity_index_marker(FP, 0) == MARKER
        assert entity_index_marker(FP, 2) == f"v{ENTITY_INDEX_VERSION}r2:{FP}"
        assert fingerprint_of(entity_index_marker(FP, 2)) == FP


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
        store = FakeStore()
        container.entity_vector_store = AsyncMock(return_value=store)
        with patch.object(mod.asyncio, "sleep", _sleep), \
             patch.object(mod, "MODEL_CHECK_SECONDS", float("inf")), \
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
        assert store.version_reads > 0

    async def test_a_model_switch_ends_the_wait_early(self) -> None:
        """Recreating the collection promptly shortens the time every other
        store refuses entity calls after a switch."""
        store = FakeStore()
        store.embedding_config_version = AsyncMock(side_effect=["a", "a", "b"])  # type: ignore[method-assign]
        sleep = AsyncMock()
        with patch.object(mod.asyncio, "sleep", sleep):
            await mod._sleep_watching_model(store, mod.IDLE_INTERVAL_SECONDS)
        assert [c.args[0] for c in sleep.await_args_list] == [mod.MODEL_CHECK_SECONDS] * 2

    async def test_an_unreadable_config_does_not_end_the_wait(self) -> None:
        store = FakeStore()
        store.embedding_config_version = AsyncMock(side_effect=["a", None, None, None])  # type: ignore[method-assign]
        sleep = AsyncMock()
        with patch.object(mod.asyncio, "sleep", sleep):
            await mod._sleep_watching_model(store, 12.0)
        assert [c.args[0] for c in sleep.await_args_list] == [5.0, 5.0, 2.0]

    async def test_only_a_leader_tick_asks_to_recreate(self) -> None:
        graph, store = FakeGraph(), FakeStore()
        assert await _rebuilder(graph, store, FakeLock(leader=False)).tick() == "not_leader"
        assert store.recreate_requests == []
        assert await _rebuilder(graph, store).tick() == "idle"
        assert store.recreate_requests == [True]

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


class TestAModelSwitchReRunsThePasses:
    """With the real store over the real config service: switching model in
    the admin UI changes the marker on the next tick, so every pass re-runs
    and rewrites the points with the new model. Before, the store kept the
    model it started with, so the marker never moved (nightly 37229085638)."""

    async def test_points_are_rewritten_with_the_new_model(self) -> None:
        from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
        from app.modules.transformers import entity_vectorstore
        from app.modules.transformers.entity_vectorstore import (
            EMBEDDING_MODEL_FIELD,
            EntityVectorStore,
        )
        from tests.support.embedding_config import (
            config_service,
            embedding_config,
            switch_embedding_model,
        )
        from tests.support.entity_vector_db import (
            FakeEmbeddingModel,
            FakeEntityVectorDB,
            embedding_models,
        )

        bge, small = FakeEmbeddingModel(1.0, 4), FakeEmbeddingModel(2.0, 6)
        db, config = FakeEntityVectorDB(), config_service()
        store = EntityVectorStore(
            logger=logging.getLogger("entity-index-test"), config_service=config,
            vector_db_service=db, recreate_on_dimension_mismatch=True,
        )
        graph = FakeGraph()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [_rec("r1", name="Q3 plan", group=None)]

        with embedding_models({"text-embedding-3-small": small}), \
                patch.object(entity_vectorstore, "get_default_embedding_model", return_value=bge):
            await _run_until_idle(_rebuilder(graph, store))
            old = entity_index_marker(f"default:{DEFAULT_EMBEDDING_MODEL}:4")
            assert graph.docs[APPS]["app-1"][EntityIndexState.STATE] == old

            await switch_embedding_model(config, embedding_config("openAI", "text-embedding-3-small"))
            outcomes = await _run_until_idle(_rebuilder(graph, store))

        new = entity_index_marker("openAI:text-embedding-3-small:6")
        assert "connector" in outcomes
        assert graph.docs[APPS]["app-1"][EntityIndexState.STATE] == new
        (point,) = db.points.values()
        assert point.payload["metadata"][EMBEDDING_MODEL_FIELD] == "openAI:text-embedding-3-small:6"
        assert point.dense_vector == small.embed_query("Q3 plan")

    async def test_a_stale_replica_cannot_overwrite_what_the_leader_refilled(self) -> None:
        """Two indexing replicas over one collection. The leader switches,
        recreates and refills; the other missed the notification and still
        embeds with the old model. Its write lands after the pass has moved
        past the row, so the pass would never repair it."""
        from app.models.entities import EntityRecord
        from app.modules.transformers import entity_vectorstore
        from app.modules.transformers.entity_vectorstore import (
            EMBEDDING_MODEL_FIELD,
            EntityVectorStore,
        )
        from tests.support.embedding_config import (
            another_process,
            config_service,
            embedding_config,
            switch_embedding_model,
        )
        from tests.support.entity_vector_db import (
            FakeEmbeddingModel,
            FakeEntityVectorDB,
            embedding_models,
        )

        small, ada = FakeEmbeddingModel(2.0, 6), FakeEmbeddingModel(3.0, 6)
        db, config_a = FakeEntityVectorDB(), config_service(embedding_config("openAI", "text-embedding-3-small"))
        config_b = another_process(config_a)

        def _replica(config: Any) -> EntityVectorStore:  # noqa: ANN401
            return EntityVectorStore(
                logger=logging.getLogger("entity-index-test"), config_service=config,
                vector_db_service=db, recreate_on_dimension_mismatch=True,
            )

        leader, other = _replica(config_a), _replica(config_b)
        graph = FakeGraph()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [_rec("r1", name="Q3 plan", group=None)]
        models = {"text-embedding-3-small": small, "text-embedding-ada-002": ada}
        with embedding_models(models), patch.object(entity_vectorstore, "get_default_embedding_model"):
            await _run_until_idle(_rebuilder(graph, leader))
            await other.embedding_fingerprint()

            await switch_embedding_model(config_a, embedding_config("openAI", "text-embedding-ada-002"))
            await _run_until_idle(_rebuilder(graph, leader))
            await other.upsert_entities_batch(
                [EntityRecord.for_record("r1", "Q3 plan", "org-1", "app-1", None)], merge_membership=False,
            )

        assert graph.docs[APPS]["app-1"][EntityIndexState.STATE] == entity_index_marker(
            "openAI:text-embedding-ada-002:6"
        )
        (point,) = db.points.values()
        assert point.payload["metadata"][EMBEDDING_MODEL_FIELD] == "openAI:text-embedding-ada-002:6"
        assert point.dense_vector == ada.embed_query("Q3 plan")


class RefillConfig:
    """The KV store as the refill state uses it."""

    def __init__(self) -> None:
        self.kv: dict[str, object] = {}
        self.fail_get = self.fail_set = False
        self.writes = 0

    async def get_config(
        self, key: str, default: object = None, use_cache: bool = False, *, raise_on_error: bool = False,
    ) -> object:
        assert use_cache is False  # another replica may have written it
        if self.fail_get:
            # Like the real one: a failed read is the default unless asked.
            if raise_on_error:
                raise RuntimeError("kv down")
            return default
        return self.kv.get(key, default)

    async def set_config(self, key: str, value: object) -> bool:
        if self.fail_set:
            return False
        self.writes += 1
        self.kv[key] = value
        return True

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return []

    @property
    def generation(self) -> int:
        state = self.kv.get(REFILL_STATE_KEY)
        return state["generation"] if isinstance(state, dict) else 0


class TestAnEmptiedIndexIsRefilled:
    """Nothing rebuilt an index emptied from outside ("Delete all embeddings"
    dropped the entities collection with the records one on some deployments):
    every document still said done, until the embedding model next changed."""

    @staticmethod
    def _done(graph: FakeGraph) -> None:
        graph.docs[APPS]["app-1"] = _app(**{EntityIndexState.STATE: MARKER, EntityIndexState.TARGET: MARKER})
        graph.docs[ORGS]["org-1"] = _done_org(**{EntityIndexState.SWEPT_AT: NOW * 2})
        graph.sources[(RECORDS, "app-1")] = [_rec("r1", name="Q3 plan")]
        graph.sources[(TOPICS, "org-1")] = [{"_key": "t1", "name": "Billing"}]
        graph.membership[("topic", "t1")] = {"connectorIds": ["app-1"], "recordGroupIds": ["g1"]}

    def _emptied(self) -> tuple[FakeGraph, FakeStore, RefillConfig, Clock, EntityIndexRebuilder]:
        graph, store, config, clock = FakeGraph(), FakeStore(), RefillConfig(), Clock()
        self._done(graph)
        store.count = 0
        return graph, store, config, clock, _rebuilder(graph, store, config_service=config, now_ms=clock)

    async def test_every_pass_runs_again_and_the_points_come_back(self) -> None:
        graph, store, config, clock, rebuilder = self._emptied()

        outcomes = await _run_until_settled(rebuilder, clock)

        assert outcomes[:2] == ["idle", "refill"]
        assert store.ensured == 1
        assert "connector" in outcomes and "taxonomy" in outcomes
        assert {(e.entity_type, e.entity_id) for e in store.written()} == {
            (EntityType.RECORD, "r1"), (EntityType.TOPIC, "t1"),
        }
        refilled = entity_index_marker(FP, 1)
        assert graph.docs[APPS]["app-1"][EntityIndexState.STATE] == refilled
        assert graph.docs[ORGS]["org-1"][EntityIndexState.STATE] == refilled

    async def test_an_index_that_only_just_read_as_empty_is_not_refilled_yet(self) -> None:
        """A count lags the writes (OpenSearch publishes them every 30s), so a
        full index counts 0 on the idle tick straight after its passes."""
        _, store, config, clock, rebuilder = self._emptied()

        assert await rebuilder.tick() == "idle"
        clock.now += EMPTY_CONFIRM_MS - 1
        assert await rebuilder.tick() == "idle"
        store.count = 2
        clock.now += EMPTY_CONFIRM_MS
        assert await rebuilder.tick() == "idle"
        store.count = 0
        assert await rebuilder.tick() == "idle"

        assert config.kv == {} and store.ensured == 0

    async def test_an_empty_reading_from_before_a_pass_does_not_count_after_it(self) -> None:
        """The pass may have filled the index; the count would not show it yet."""
        graph, store, config, clock, rebuilder = self._emptied()
        assert await rebuilder.tick() == "idle"

        graph.docs[APPS]["app-2"] = _app("app-2")
        clock.now += EMPTY_CONFIRM_MS
        for _ in range(2):
            assert await rebuilder.tick() == "connector"
        assert await rebuilder.tick() == "idle"

        assert config.kv == {}

    async def test_a_deployment_with_nothing_to_index_is_refilled_once(self) -> None:
        """The index stays empty after the refill; a second one would be the
        first again, for ever."""
        graph, store, config, clock = FakeGraph(), FakeStore(), RefillConfig(), Clock()
        graph.docs[APPS]["app-1"] = _app(**{EntityIndexState.STATE: MARKER})
        graph.docs[ORGS]["org-1"] = _done_org(**{EntityIndexState.SWEPT_AT: NOW * 2})
        store.count = 0
        rebuilder = _rebuilder(graph, store, config_service=config, now_ms=clock)

        outcomes = []
        for _ in range(60):
            outcomes.append(await rebuilder.tick())
            if outcomes[-1] == "idle":
                clock.now += EMPTY_CONFIRM_MS

        assert outcomes.count("refill") == 1
        assert outcomes[-30:] == ["idle"] * 30
        assert config.generation == 1 and config.writes == 1
        assert store.written() == []

    async def test_a_restarted_or_new_leader_does_not_refill_again(self) -> None:
        """The guard is in the KV store, not in the process that refilled."""
        graph, store, config, clock, rebuilder = self._emptied()
        await _run_until_settled(rebuilder, clock)

        outcomes = await _run_until_settled(_rebuilder(graph, store, config_service=config, now_ms=clock), clock)

        assert outcomes == ["idle", "idle"] and config.generation == 1

    async def test_an_index_emptied_again_after_holding_points_is_refilled_again(self) -> None:
        _, store, config, clock, rebuilder = self._emptied()
        await _run_until_settled(rebuilder, clock)

        store.count = 2
        assert await rebuilder.tick() == "idle"
        assert config.kv[REFILL_STATE_KEY] == {"generation": 1, "emptySinceRefill": False}
        store.count = 0
        outcomes = await _run_until_settled(rebuilder, clock)

        assert outcomes[:2] == ["idle", "refill"]
        assert config.kv[REFILL_STATE_KEY] == {"generation": 2, "emptySinceRefill": True}

    async def test_a_populated_index_is_only_counted(self) -> None:
        graph, store, config, clock = FakeGraph(), FakeStore(), RefillConfig(), Clock()
        self._done(graph)
        rebuilder = _rebuilder(graph, store, config_service=config, now_ms=clock)

        assert await _run_until_settled(rebuilder, clock) == ["idle", "idle"]

        assert store.count_reads == 2 and store.ensured == 0
        assert config.kv == {} and graph.updates == []

    async def test_an_empty_index_is_not_refilled_while_a_pass_is_still_due(self) -> None:
        """The pass under way fills it; counting is for when nothing else will."""
        graph, store, config = FakeGraph(), FakeStore(), RefillConfig()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [_rec("r1")]
        store.count = 0

        assert await _rebuilder(graph, store, config_service=config).tick() == "connector"

        assert store.count_reads == 0 and config.kv == {}

    async def test_a_replica_that_is_not_leader_counts_nothing(self) -> None:
        graph, store, config = FakeGraph(), FakeStore(), RefillConfig()
        self._done(graph)
        store.count = 0

        assert await _rebuilder(graph, store, FakeLock(leader=False), config_service=config).tick() == "not_leader"

        assert store.count_reads == 0 and config.kv == {}

    async def test_a_refill_that_cannot_be_recorded_does_not_start(self) -> None:
        """Started without its guard written, it would start again every tick."""
        graph, store, config, clock, rebuilder = self._emptied()
        config.fail_set = True

        assert await _run_until_settled(rebuilder, clock) == ["idle", "idle"]
        assert graph.updates == [] and store.written() == []

        config.fail_set = False
        assert await rebuilder.tick() == "refill"

    async def test_a_count_that_fails_is_tried_again_next_tick(self) -> None:
        _, store, config, clock, rebuilder = self._emptied()
        store.points_count = AsyncMock(side_effect=RuntimeError("vector db down"))

        assert await _run_until_settled(rebuilder, clock) == ["idle", "idle"]
        assert config.kv == {}

        store.points_count = AsyncMock(return_value=0)
        assert (await _run_until_settled(rebuilder, clock))[:2] == ["idle", "refill"]

    async def test_a_collection_that_cannot_be_set_up_is_not_refilled_yet(self) -> None:
        _, store, config, clock, rebuilder = self._emptied()
        store.ensure_collection = AsyncMock(side_effect=RuntimeError("vector db down"))

        assert await _run_until_settled(rebuilder, clock) == ["idle", "idle"]

        assert config.kv == {}

    async def test_an_unreadable_refill_count_fails_the_tick(self) -> None:
        """Assumed to be 0, it would name a marker the documents left at the
        last refill, and every pass would run again under it."""
        graph, store, config = FakeGraph(), FakeStore(), RefillConfig()
        self._done(graph)
        config.kv[REFILL_STATE_KEY] = {"generation": 3, "emptySinceRefill": False}
        config.fail_get = True

        with pytest.raises(RuntimeError, match="kv down"):
            await _rebuilder(graph, store, config_service=config).tick()

        assert store.recreate_requests == [] and graph.updates == []

    async def test_the_stored_refill_count_is_part_of_the_marker(self) -> None:
        graph, store, config = FakeGraph(), FakeStore(), RefillConfig()
        graph.docs[APPS]["app-1"] = _app(**{EntityIndexState.STATE: MARKER})
        graph.sources[(RECORDS, "app-1")] = [_rec("r1")]
        config.kv[REFILL_STATE_KEY] = {"generation": 3, "emptySinceRefill": False}

        assert await _rebuilder(graph, store, config_service=config).tick() == "connector"

        assert graph.docs[APPS]["app-1"][EntityIndexState.TARGET] == entity_index_marker(FP, 3)


class TestACollectionDroppedUnderTheRunningStore:
    """With the real store: it sets its collection up when it initialises, so
    one dropped afterwards stayed missing, and every write and search failed,
    until the service restarted."""

    async def test_it_is_created_again_and_refilled(self) -> None:
        from app.modules.transformers import entity_vectorstore
        from app.modules.transformers.entity_vectorstore import EntityVectorStore
        from tests.support.embedding_config import config_service
        from tests.support.entity_vector_db import (
            FakeEmbeddingModel,
            FakeEntityVectorDB,
        )

        db, config, clock = FakeEntityVectorDB(), config_service(), Clock()
        store = EntityVectorStore(
            logger=logging.getLogger("entity-index-test"), config_service=config,
            vector_db_service=db, recreate_on_dimension_mismatch=True,
        )
        graph = FakeGraph()
        graph.docs[APPS]["app-1"] = _app()
        graph.sources[(RECORDS, "app-1")] = [_rec("r1", name="Q3 plan", group=None)]
        rebuilder = _rebuilder(graph, store, config_service=config, now_ms=clock)

        with patch.object(
            entity_vectorstore, "get_default_embedding_model", return_value=FakeEmbeddingModel(1.0, 4),
        ):
            await _run_until_settled(rebuilder, clock)
            before = dict(db.points)
            assert len(before) == 1

            await db.delete_collection("entities")
            outcomes = await _run_until_settled(rebuilder, clock)

        assert outcomes[:2] == ["idle", "refill"] and "connector" in outcomes
        assert db.dimension == 4
        assert db.points.keys() == before.keys()

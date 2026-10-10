"""The stored-content heal: indexed records whose stored copy is gone from
storage get one forced reindex per virtual record id, once per version.

The graph keeps apps and records in memory and answers the reads the sweep
pages with; storage answers which document ids are missing; the reindex goes
through the real ``StorageCleanupHelper`` holder logic.
"""
from __future__ import annotations

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.storage_cleanup import (
    MissingDocumentsRouteUnavailable,
    StorageCleanupHelper,
)
from app.connectors.services.stored_content_heal import (
    HEAL_VERSION,
    StoredContentHeal,
    StoredContentHealState,
)

APPS = CollectionNames.APPS.value
RECORDS = CollectionNames.RECORDS.value
ORG = "65f000000000000000000001"


class HealGraph:
    def __init__(self) -> None:
        self.apps: dict[str, dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {}

    async def get_all_documents(self, collection, transaction=None) -> list[dict[str, Any]]:
        assert collection == APPS
        return [copy.deepcopy(doc) for doc in self.apps.values()]

    async def page_records_for_vector_membership_backfill(
        self, connector_id, after_key, limit, transaction=None,
    ) -> list[dict[str, Any]]:
        keys = sorted(
            k for k, r in self.records.items()
            if r["connectorId"] == connector_id and (after_key is None or k > after_key)
        )
        return [
            {
                "_key": k,
                "virtualRecordId": self.records[k].get("virtualRecordId"),
                "orgId": self.records[k].get("orgId"),
                "indexingStatus": self.records[k].get("indexingStatus"),
                "isDeleted": self.records[k].get("isDeleted"),
            }
            for k in keys[:limit]
        ]

    async def get_document(self, key, collection, transaction=None, **_kw) -> dict[str, Any] | None:
        source = self.apps if collection == APPS else self.records if collection == RECORDS else {}
        doc = source.get(key)
        return {**copy.deepcopy(doc), "_key": key} if doc is not None else None

    async def update_node(self, key, collection, node_updates, transaction=None) -> bool:
        target = self.apps if collection == APPS else self.records
        target[key].update(node_updates)
        return True

    async def get_records_by_virtual_record_id(self, vrid, **_kw) -> list[str]:
        return sorted(
            k for k, r in self.records.items()
            if r.get("virtualRecordId") == vrid and not r.get("isDeleted")
        )

    async def _create_reindex_event_payload(self, record, file_record) -> dict[str, Any]:
        return {"recordId": record["_key"]}


class FakeBlobStore:
    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}
        self.lookups: list[list[str]] = []

    async def get_document_ids_by_virtual_record_ids(self, vrids) -> dict[str, dict]:
        self.lookups.append(list(vrids))
        return {v: {"record_doc_id": self.mapping[v]} for v in vrids if v in self.mapping}


class AlwaysLeader:
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True

    async def release(self) -> None:
        pass

    async def close(self) -> None:
        pass


class World:
    def __init__(self) -> None:
        self.graph = HealGraph()
        self.blob = FakeBlobStore()
        self.existing: set[str] = set()
        self.lookups: list[tuple[str, list[str]]] = []
        self.events: list[tuple[str, dict]] = []
        self.helper = StorageCleanupHelper(logging.getLogger("heal-test"), self.graph, MagicMock())
        self.helper.find_missing_documents = self._find_missing

    async def _find_missing(self, org_id, document_ids) -> list[str]:
        self.lookups.append((org_id, list(document_ids)))
        return [d for d in document_ids if d not in self.existing]

    async def publish(self, topic, event) -> bool:
        self.events.append((topic, event))
        return True

    def app(self, key: str) -> None:
        self.graph.apps[key] = {"_key": key, "orgId": ORG}

    def record(self, key: str, connector: str, vrid: str | None, *, doc: str | None = None,
               exists: bool = True, status: str = "COMPLETED", deleted: bool = False) -> None:
        self.graph.records[key] = {
            "connectorId": connector, "virtualRecordId": vrid, "orgId": ORG,
            "indexingStatus": status, "isDeleted": deleted, "recordType": "MAIL",
        }
        if vrid and doc:
            self.blob.mapping[vrid] = doc
            if exists:
                self.existing.add(doc)

    def heal(self, **kwargs) -> StoredContentHeal:
        return StoredContentHeal(
            logger=logging.getLogger("heal-test"), graph_provider=self.graph, blob_store=self.blob,
            storage=self.helper, publish=self.publish, lock=AlwaysLeader(), **kwargs,
        )

    def reindexed(self) -> list[str]:
        return [event["payload"]["recordId"] for _, event in self.events]


async def _run_until_idle(heal: StoredContentHeal) -> list[str]:
    outcomes = []
    for _ in range(100):
        outcome = await heal.tick()
        outcomes.append(outcome)
        if outcome == "idle":
            return outcomes
    raise AssertionError(f"heal never went idle: {outcomes}")


@pytest.fixture
def world() -> World:
    w = World()
    w.app("conn-a")
    w.app("conn-b")
    return w


class TestMissingContentIsRebuilt:
    async def test_one_forced_reindex_per_missing_vrid_across_pages_and_connectors(self, world) -> None:
        # Three holders of one lost copy, split over pages and connectors.
        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)
        world.record("a2", "conn-a", "v-ok", doc="d-ok")
        world.record("a3", "conn-a", "v-lost", doc="d-lost", exists=False)
        world.record("b1", "conn-b", "v-lost", doc="d-lost", exists=False)

        await _run_until_idle(world.heal(page_size=1))

        assert world.reindexed() == ["a1"]
        topic, event = world.events[0]
        assert topic == "record-events"
        assert event["eventType"] == "newRecord"
        assert event["payload"]["forceReindex"] is True
        app = world.graph.apps["conn-a"]
        assert app[StoredContentHealState.STATE] == HEAL_VERSION
        assert app[StoredContentHealState.HEALED] == 1
        assert app[StoredContentHealState.MISSING] == 1
        assert world.graph.apps["conn-b"][StoredContentHealState.HEALED] == 0

    async def test_a_record_whose_document_exists_is_untouched(self, world) -> None:
        world.record("a1", "conn-a", "v-ok", doc="d-ok")

        await _run_until_idle(world.heal())

        assert world.events == []
        assert world.lookups == [(ORG, ["d-ok"])]
        assert world.graph.apps["conn-a"][StoredContentHealState.CHECKED] == 1

    async def test_records_that_are_not_indexed_are_skipped(self, world) -> None:
        world.record("a1", "conn-a", "v-queued", doc="d-1", exists=False, status="QUEUED")
        world.record("a2", "conn-a", "v-failed", doc="d-2", exists=False, status="FAILED")
        world.record("a3", "conn-a", "v-trash", doc="d-3", exists=False, deleted=True)
        world.record("a4", "conn-a", None)

        await _run_until_idle(world.heal())

        assert world.events == []
        assert world.lookups == []

    async def test_one_storage_lookup_per_page(self, world) -> None:
        for i in range(5):
            world.record(f"a{i}", "conn-a", f"v{i}", doc=f"d{i}")

        await _run_until_idle(world.heal(page_size=5))

        assert world.lookups == [(ORG, ["d0", "d1", "d2", "d3", "d4"])]

    async def test_publishes_are_capped_per_tick_and_the_rest_resume_next_tick(self, world) -> None:
        for i in range(5):
            world.record(f"a{i}", "conn-a", f"v{i}", doc=f"d{i}", exists=False)
        heal = world.heal(page_size=5, max_reindex_per_tick=2)

        assert await heal.tick() == "page"
        assert world.reindexed() == ["a0", "a1"]
        assert world.graph.apps["conn-a"][StoredContentHealState.AFTER_KEY] == "a1"

        await _run_until_idle(heal)

        assert world.reindexed() == ["a0", "a1", "a2", "a3", "a4"]
        assert world.graph.apps["conn-a"][StoredContentHealState.HEALED] == 5
        assert world.graph.apps["conn-a"][StoredContentHealState.MISSING] == 5


class TestRunsOncePerVersion:
    async def test_finishes_and_does_not_run_again_for_the_same_version(self, world) -> None:
        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)

        assert (await _run_until_idle(world.heal()))[-1] == "idle"
        lookups = len(world.lookups)

        # A restart: a fresh process with nothing remembered.
        assert await _run_until_idle(world.heal()) == ["idle"]
        assert len(world.lookups) == lookups
        assert world.reindexed() == ["a1"]

    async def test_a_deleting_connector_is_not_swept(self, world) -> None:
        world.graph.apps["conn-a"]["status"] = "DELETING"
        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)

        await _run_until_idle(world.heal())

        assert world.events == []


class TestCompatibilityAndFailures:
    async def test_a_node_without_the_route_defers_and_never_marks_done(self, world) -> None:
        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)

        async def _no_route(org_id, document_ids) -> list[str]:
            raise MissingDocumentsRouteUnavailable(404)

        world.helper.find_missing_documents = _no_route
        heal = world.heal()

        assert [await heal.tick() for _ in range(3)] == ["deferred"] * 3
        app = world.graph.apps["conn-a"]
        assert app.get(StoredContentHealState.STATE) is None
        assert app.get(StoredContentHealState.AFTER_KEY) is None
        assert world.events == []

    async def test_a_failing_storage_lookup_raises_and_marks_nothing(self, world) -> None:
        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)
        world.helper.find_missing_documents = AsyncMock(side_effect=RuntimeError("503"))

        with pytest.raises(RuntimeError):
            await world.heal().tick()

        assert world.graph.apps["conn-a"].get(StoredContentHealState.STATE) is None

    async def test_a_failed_publish_is_retried_then_given_up_with_the_count_kept(self, world) -> None:
        from app.connectors.services.stored_content_heal import MAX_ATTEMPTS

        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)

        async def _refuse(topic, event) -> bool:
            world.events.append((topic, event))
            return False

        world.publish = _refuse
        await _run_until_idle(world.heal())

        app = world.graph.apps["conn-a"]
        assert len(world.events) == MAX_ATTEMPTS
        assert app[StoredContentHealState.STATE] == HEAL_VERSION
        assert app[StoredContentHealState.EXHAUSTED] is True
        assert app[StoredContentHealState.FAILURES] == 1

    async def test_a_vrid_with_no_live_holder_is_counted_and_left(self, world) -> None:
        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)
        world.graph.get_records_by_virtual_record_id = AsyncMock(return_value=[])

        await _run_until_idle(world.heal())

        app = world.graph.apps["conn-a"]
        assert world.events == []
        assert app[StoredContentHealState.ORPHANED] == 1
        assert app[StoredContentHealState.FAILURES] == 0
        assert app[StoredContentHealState.STATE] == HEAL_VERSION


class TestLoop:
    def test_the_connectors_service_starts_and_stops_the_heal_loop(self) -> None:
        import inspect

        from app import connectors_main

        source = inspect.getsource(connectors_main)
        assert "run_stored_content_heal_loop(app_container, graph_provider)" in source
        assert '"stored_content_heal_task"' in source

    async def test_the_loop_ends_once_every_connector_is_done(self, world, monkeypatch) -> None:
        from app.connectors.services import stored_content_heal as mod

        world.record("a1", "conn-a", "v-lost", doc="d-lost", exists=False)
        monkeypatch.setattr(mod.MessagingUtils, "_get_redis_config", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(mod, "VectorMembershipBackfillLeaderLock", lambda *a, **k: AlwaysLeader())
        monkeypatch.setattr(mod, "StorageCleanupHelper", lambda *a, **k: world.helper)
        monkeypatch.setattr(mod, "BlobStorage", lambda *a, **k: world.blob)
        container = MagicMock()
        container.logger.return_value = logging.getLogger("heal-test")
        container.messaging_producer.send_message = AsyncMock(return_value=True)

        await mod.run_stored_content_heal_loop(container, world.graph, sleep=AsyncMock())

        assert world.graph.apps["conn-a"][StoredContentHealState.STATE] == HEAL_VERSION
        sent = container.messaging_producer.send_message.await_args.kwargs
        assert sent["topic"] == "record-events"
        assert sent["message"]["payload"]["recordId"] == "a1"


def test_the_strict_arango_app_schema_accepts_every_heal_field() -> None:
    from app.schema.arango.documents import app_schema

    properties = app_schema["rule"]["properties"]
    for name, value in vars(StoredContentHealState).items():
        if not name.startswith("_"):
            assert value in properties, value

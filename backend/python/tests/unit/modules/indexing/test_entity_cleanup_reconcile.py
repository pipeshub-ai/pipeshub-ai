"""The rebuild loop runs a deleted connector's entity cleanup that its
deleteConnectorEntities event never did (lost publish, dead-lettered), from
the intent the deleting service recorded first."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

from app.config.constants.arangodb import CollectionNames
from app.connectors.services.entity_cleanup_intents import PENDING_DIRECTORY
from app.modules.indexing.entity_index_rebuild import (
    ENTITY_CLEANUP_CHECK_MS,
    ENTITY_CLEANUP_GRACE_MS,
    ENTITY_CLEANUP_STALE_MS,
    EntityIndexRebuilder,
)
from tests.unit.connectors.services.test_entity_cleanup_intents import FakeConfig
from tests.unit.modules.indexing.test_entity_index_rebuild import (
    MARKER,
    NOW,
    FakeGraph,
    FakeLock,
    FakeStore,
    _done_org,
)

APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value
OLD = NOW - ENTITY_CLEANUP_GRACE_MS - 1


class Graph(FakeGraph):
    fail_get_document = False

    async def get_document(self, key: str, collection: str, transaction: str | None = None,
                           *, raise_on_error: bool = False) -> dict | None:
        if self.fail_get_document:
            # Like both providers: a failed read is None unless the caller asks.
            if raise_on_error:
                raise RuntimeError("graph down")
            return None
        doc = self.docs.get(collection, {}).get(key)
        return dict(doc, _key=key) if doc else None


class Store(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.cleanups: list[dict[str, Any]] = []
        self.fail_cleanup = False

    async def delete_entities_by_connector(self, org_id: str, connector_id: str, record_group_ids=None,
                                           membership_lookup=None) -> None:
        if self.fail_cleanup:
            raise RuntimeError("vector db down")
        self.cleanups.append({"org": org_id, "connector": connector_id, "groups": record_group_ids,
                              "lookup": membership_lookup})


def _setup(*intents: dict, clock: dict | None = None) -> tuple[Graph, Store, FakeConfig, EntityIndexRebuilder]:
    graph, store, config = Graph(), Store(), FakeConfig()
    clock = clock if clock is not None else {"now": NOW}
    graph.docs[ORGS]["org-1"] = _done_org()  # nothing else to do
    for intent in intents:
        config.kv[f"{PENDING_DIRECTORY}{intent['connectorId']}"] = intent
    rebuilder = EntityIndexRebuilder(
        logger=logging.getLogger("t"), graph_provider=graph, store=store, lock=FakeLock(),
        config_service=config, now_ms=lambda: clock["now"],
    )
    return graph, store, config, rebuilder


def _intent(connector: str, at: int = OLD, **extra: object) -> dict:
    return {"orgId": "org-1", "connectorId": connector, "connectorName": "DRIVE", "requestedAt": at, **extra}


async def test_a_deleted_connectors_left_over_cleanup_runs_and_is_cleared() -> None:
    graph, store, config, rebuilder = _setup(_intent("gone"))
    graph.get_taxonomy_entity_membership = AsyncMock(return_value={})
    assert await rebuilder.tick() == "entity_cleanup"
    (call,) = store.cleanups
    # The groups are gone from the graph; the store reads them from its points.
    assert (call["org"], call["connector"], call["groups"]) == ("org-1", "gone", None)
    await call["lookup"]([{"id": "t1", "type": "topic"}])
    graph.get_taxonomy_entity_membership.assert_awaited_once_with([{"id": "t1", "type": "topic"}], "org-1")
    assert config.kv == {}


async def test_a_recent_intent_is_left_to_its_event() -> None:
    _, store, config, rebuilder = _setup(_intent("gone", at=NOW - 1000))
    assert await rebuilder.tick() != "entity_cleanup"
    assert store.cleanups == [] and len(config.kv) == 1


async def test_a_connector_that_still_exists_is_never_cleaned() -> None:
    """Its delete failed and was reverted: the intent is stale, and dropped
    once no delete can plausibly still be running (a KB has no DELETING)."""
    graph, store, config, rebuilder = _setup(_intent("live", at=NOW - ENTITY_CLEANUP_STALE_MS - 1))
    graph.docs[APPS]["live"] = {"name": "live", "status": None}
    assert await rebuilder.tick() == "entity_cleanup"
    assert store.cleanups == [] and config.kv == {}


async def test_an_existing_app_is_waited_for_before_its_intent_is_dropped() -> None:
    """A long KB delete still shows its app; its intent must survive it."""
    graph, store, config, rebuilder = _setup(_intent("kb"))
    graph.docs[APPS]["kb"] = {"name": "kb", "status": None}
    assert await rebuilder.tick() != "entity_cleanup"
    assert store.cleanups == [] and len(config.kv) == 1


async def test_a_failed_app_read_never_counts_as_gone() -> None:
    """A transient graph error must not wipe a live connector's points."""
    graph, store, config, rebuilder = _setup(_intent("live"))
    graph.docs[APPS]["live"] = {"name": "live", "status": None}
    graph.fail_get_document = True
    assert await rebuilder.tick() != "entity_cleanup"
    assert store.cleanups == []
    (intent,) = config.kv.values()
    assert intent["attempts"] == 1


async def test_intents_are_read_at_most_once_per_check_interval() -> None:
    """Listing them scans the whole KV store; the grace is minutes long."""
    clock = {"now": NOW}
    _, _, config, rebuilder = _setup(_intent("gone", at=NOW - 1000), clock=clock)
    reads = {"n": 0}
    real = config.list_keys_in_directory

    async def counting(directory: str) -> list[str]:
        reads["n"] += 1
        return await real(directory)

    config.list_keys_in_directory = counting
    await rebuilder.tick()
    await rebuilder.tick()
    assert reads["n"] == 1
    clock["now"] += ENTITY_CLEANUP_CHECK_MS
    await rebuilder.tick()
    assert reads["n"] == 2


async def test_a_connector_still_being_deleted_is_waited_for() -> None:
    graph, store, config, rebuilder = _setup(_intent("deleting"))
    graph.docs[APPS]["deleting"] = {"name": "x", "status": "DELETING"}
    assert await rebuilder.tick() != "entity_cleanup"
    assert store.cleanups == [] and len(config.kv) == 1


async def test_a_failed_cleanup_is_kept_and_backed_off() -> None:
    _, store, config, rebuilder = _setup(_intent("gone"))
    store.fail_cleanup = True
    assert await rebuilder.tick() != "entity_cleanup"  # the rest of the loop still runs
    (intent,) = config.kv.values()
    assert intent["attempts"] == 1 and intent["nextAttemptAt"] > NOW
    store.fail_cleanup = False
    assert await rebuilder.tick() != "entity_cleanup"  # not before its backoff
    assert store.cleanups == []


async def test_an_unreadable_kv_store_does_not_stop_the_rebuild() -> None:
    _, _, config, rebuilder = _setup(_intent("gone"))
    config.fail_list = True
    assert await rebuilder.tick() in ("idle", "sweep", "taxonomy", "connector")


async def test_without_a_kv_store_nothing_is_reconciled() -> None:
    graph, store = Graph(), Store()
    graph.docs[ORGS]["org-1"] = _done_org()
    rebuilder = EntityIndexRebuilder(
        logger=logging.getLogger("t"), graph_provider=graph, store=store, lock=FakeLock(), now_ms=lambda: NOW,
    )
    assert await rebuilder.tick() != "entity_cleanup"
    assert MARKER  # the marker helper is shared with the rebuild tests

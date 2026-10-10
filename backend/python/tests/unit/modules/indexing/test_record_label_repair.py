"""The record label repair: a record's stored labels are restored from the
spellings on its own taxonomy edges, one page of one connector per tick.

The graph is the in-memory entity graph plus the reads the repair pages
with; the blob store keeps one stored record per virtual record id.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.indexing.record_label_repair import (
    REPAIR_VERSION,
    RecordLabelRepair,
    RecordLabelRepairState,
    own_label_fields,
)
from app.services.graph_db.taxonomy import TaxonomyLink
from tests.support.fake_entity_graph import FakeGraph

APPS = CollectionNames.APPS.value
RECORDS = CollectionNames.RECORDS.value
TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value
SUB1 = CollectionNames.SUBCATEGORIES1.value
LANGUAGES = CollectionNames.LANGUAGES.value
BELONGS_TO_TOPIC = CollectionNames.BELONGS_TO_TOPIC.value
BELONGS_TO_CATEGORY = CollectionNames.BELONGS_TO_CATEGORY.value
BELONGS_TO_LANGUAGE = CollectionNames.BELONGS_TO_LANGUAGE.value

ORG = "acme"
CONNECTOR = "conn-1"
CUTOFF = 2_000
BEFORE = 1_000


class RepairGraph(FakeGraph):
    def __init__(self) -> None:
        super().__init__()
        self.apps: dict[str, dict[str, Any]] = {}
        self.link_calls: list[list[str]] = []

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
        return [{"_key": k, "virtualRecordId": self.records[k]["virtualRecordId"]} for k in keys[:limit]]

    async def get_record_taxonomy_links(self, record_keys, transaction=None) -> list[dict[str, Any]]:
        self.link_calls.append(list(record_keys))
        return await super().get_record_taxonomy_links(record_keys, transaction)

    async def get_document(
        self, document_key, collection, transaction=None, *, raise_on_error=False,
    ) -> dict[str, Any] | None:
        source = self.apps if collection == APPS else self.records
        doc = source.get(document_key)
        return copy.deepcopy(doc) if doc is not None else None

    async def update_node(self, key, collection, node_updates, transaction=None) -> bool:
        target = self.apps if collection == APPS else self.records
        target[key].update(node_updates)
        return True


class FakeBlobStore:
    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any]] = {}
        self.reads: list[str] = []
        self.writes: list[tuple[str, str, str]] = []

    async def get_document_id_by_virtual_record_id(self, virtual_record_id) -> dict | None:
        if virtual_record_id not in self.stored:
            return None
        return {"record_doc_id": f"doc-{virtual_record_id}", "fileSizeBytes": 10}

    async def get_record_from_storage(self, virtual_record_id, org_id, lookup_result=None) -> dict | None:
        self.reads.append(virtual_record_id)
        record = self.stored.get(virtual_record_id)
        return copy.deepcopy(record) if record is not None else None

    async def update_record_buffer(self, org_id, document_id, record_dict, virtual_record_id) -> tuple[str, int]:
        self.writes.append((org_id, document_id, virtual_record_id))
        self.stored[virtual_record_id] = copy.deepcopy(record_dict)
        return document_id, 10


class AlwaysLeader:
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True

    async def release(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _canonical(graph: RepairGraph, collection: str, key: str, name: str) -> None:
    graph.nodes[(collection, key)] = {"name": name, "normalizedName": name.casefold(), "orgId": ORG}


def _record(graph: RepairGraph, blob: FakeBlobStore, key: str, semantic: dict[str, Any], *,
            extracted_at: int = BEFORE) -> None:
    graph.add_record(key, ORG, CONNECTOR)
    graph.records[key].update({
        "virtualRecordId": f"vr-{key}", "lastExtractionTimestamp": extracted_at,
        "processingStartedAt": None, "indexingStatus": "COMPLETED",
    })
    blob.stored[f"vr-{key}"] = {
        "id": key, "record_name": key, "org_id": ORG,
        "block_containers": {"blocks": [{"text": f"content of {key}"}]},
        "semantic_metadata": {"summary": f"summary of {key}", **semantic},
    }


def _link(graph: RepairGraph, record: str, edge_collection: str, collection: str, key: str,
          extracted: str | None) -> None:
    edge = {"from_id": record, "to_id": key, "createdAtTimestamp": 1}
    if extracted is not None:
        edge["extractedName"] = extracted
    graph.edges[(edge_collection, f"{RECORDS}/{record}", f"{collection}/{key}")] = edge


@pytest.fixture
def world() -> tuple[RepairGraph, FakeBlobStore]:
    graph, blob = RepairGraph(), FakeBlobStore()
    graph.apps[CONNECTOR] = {"_key": CONNECTOR, "orgId": ORG}
    _canonical(graph, TOPICS, "t-launch", "Falcon launch window")
    _canonical(graph, TOPICS, "t-dates", "Shipping dates")
    _canonical(graph, CATEGORIES, "c-prog", "Codename Falcon programme")
    _canonical(graph, LANGUAGES, "l-en", "English")
    graph.nodes[(TOPICS, "t-legacy")] = {"name": "Legacy topic"}

    # Indexed second: its labels were rewritten to the first record's names.
    _record(graph, blob, "b-open", {
        "categories": ["Codename Falcon programme"],
        "topics": ["Shipping dates", "Falcon launch window"],
        "languages": ["English"],
    })
    _link(graph, "b-open", BELONGS_TO_CATEGORY, CATEGORIES, "c-prog", "Product programme")
    _link(graph, "b-open", BELONGS_TO_TOPIC, TOPICS, "t-dates", "Shipping dates")
    _link(graph, "b-open", BELONGS_TO_TOPIC, TOPICS, "t-launch", "Product launch window")
    _link(graph, "b-open", BELONGS_TO_LANGUAGE, LANGUAGES, "l-en", "English")

    # A deduplicated copy: its edges were copied without the spelling.
    _record(graph, blob, "c-copy", {"topics": ["Falcon launch window"]})
    _link(graph, "c-copy", BELONGS_TO_TOPIC, TOPICS, "t-launch", None)

    # Indexed before resolution existed: a legacy node, never rewritten.
    _record(graph, blob, "d-legacy", {"topics": ["Legacy topic"]})
    _link(graph, "d-legacy", BELONGS_TO_TOPIC, TOPICS, "t-legacy", None)

    # Extracted after the repair started: already carries its own labels.
    _record(graph, blob, "e-recent", {"topics": ["Product launch window"]}, extracted_at=CUTOFF + 5)
    _link(graph, "e-recent", BELONGS_TO_TOPIC, TOPICS, "t-launch", "Product launch window")
    return graph, blob


async def _run_until_idle(graph, blob, *, page_size=2, logger=None) -> list[str]:
    repair = RecordLabelRepair(
        logger=logger or logging.getLogger("label-repair-test"), graph_provider=graph,
        blob_store=blob, lock=AlwaysLeader(), cutoff_ms=CUTOFF, page_size=page_size,
    )
    outcomes = []
    for _ in range(50):
        outcome = await repair.tick()
        outcomes.append(outcome)
        if outcome == "idle":
            return outcomes
    raise AssertionError(f"repair never went idle: {outcomes}")


class TestARecordGetsItsOwnLabelsBackFromItsEdges:
    async def test_rewritten_labels_are_restored_and_the_rest_is_kept(self, world) -> None:
        graph, blob = world
        before = copy.deepcopy(blob.stored["vr-b-open"])

        await _run_until_idle(graph, blob)

        after = blob.stored["vr-b-open"]
        assert after["semantic_metadata"]["categories"] == ["Product programme"]
        assert after["semantic_metadata"]["topics"] == ["Shipping dates", "Product launch window"]
        assert after["semantic_metadata"]["languages"] == ["English"]
        assert after["semantic_metadata"]["own_labels"] is True
        assert after["semantic_metadata"]["summary"] == before["semantic_metadata"]["summary"]
        assert after["block_containers"] == before["block_containers"]
        assert [w[2] for w in blob.writes] == ["vr-b-open"]

    async def test_a_second_run_changes_nothing(self, world) -> None:
        graph, blob = world
        await _run_until_idle(graph, blob)
        restored = copy.deepcopy(blob.stored)
        writes = len(blob.writes)

        graph.apps[CONNECTOR][RecordLabelRepairState.STATE] = None
        await _run_until_idle(graph, blob)

        assert blob.stored == restored
        assert len(blob.writes) == writes

    async def test_a_record_without_its_spelling_is_skipped_and_counted(self, world, caplog) -> None:
        graph, blob = world
        with caplog.at_level(logging.INFO, logger="label-repair-test"):
            await _run_until_idle(graph, blob)

        assert blob.stored["vr-c-copy"]["semantic_metadata"]["topics"] == ["Falcon launch window"]
        app = graph.apps[CONNECTOR]
        assert app[RecordLabelRepairState.STATE] == REPAIR_VERSION
        assert app[RecordLabelRepairState.REPAIRED] == 1
        assert app[RecordLabelRepairState.SKIPPED] == 1
        assert any("left for a reindex" in r.getMessage() for r in caplog.records)

    async def test_records_that_need_nothing_are_not_read_from_storage(self, world) -> None:
        graph, blob = world
        await _run_until_idle(graph, blob)
        assert "vr-d-legacy" not in blob.reads
        assert "vr-e-recent" not in blob.reads

    async def test_a_record_being_indexed_is_left_alone(self, world) -> None:
        graph, blob = world
        graph.records["b-open"]["processingStartedAt"] = CUTOFF - 1
        await _run_until_idle(graph, blob)
        assert blob.writes == []

    async def test_progress_is_kept_on_the_connector_between_pages(self, world) -> None:
        graph, blob = world
        outcomes = await _run_until_idle(graph, blob, page_size=1)
        assert outcomes.count("page") >= 4
        assert graph.apps[CONNECTOR][RecordLabelRepairState.AFTER_KEY] is None


class TestOwnLabelFields:
    def _links(self, *rows: tuple[str, str, str, str | None]) -> list[TaxonomyLink]:
        return [
            link
            for c, k, n, e in rows
            if (link := TaxonomyLink.from_row({
                "recordId": "r", "collection": c, "entityId": k, "name": n, "extractedName": e,
                "canonical": True, "migrated": False,
            }))
        ]

    def test_the_subcategory_chain_follows_the_category(self) -> None:
        fields = own_label_fields(
            {"categories": ["X"], "sub_category_level_1": "Y"},
            self._links((CATEGORIES, "c", "X", "Own category"), (SUB1, "s", "Y", "Own sub")),
        )
        assert fields["categories"] == ["Own category"]
        assert fields["sub_category_level_1"] == "Own sub"

    def test_unknown_spelling_on_a_canonical_node_means_no_change(self) -> None:
        assert own_label_fields({"topics": ["X"]}, self._links((TOPICS, "t", "X", None))) is None


class TestFailures:
    async def test_a_failing_write_is_retried_then_given_up_with_the_count_kept(self, world) -> None:
        from app.modules.indexing.record_label_repair import MAX_ATTEMPTS

        graph, blob = world

        async def _refuse(*_args, **_kwargs) -> None:
            raise RuntimeError("storage down")

        blob.update_record_buffer = _refuse
        await _run_until_idle(graph, blob)

        app = graph.apps[CONNECTOR]
        assert app[RecordLabelRepairState.STATE] == REPAIR_VERSION
        assert app[RecordLabelRepairState.EXHAUSTED] is True
        assert app[RecordLabelRepairState.ATTEMPTS] == MAX_ATTEMPTS
        assert app[RecordLabelRepairState.FAILURES] == 1

    async def test_a_deleting_connector_is_not_repaired(self, world) -> None:
        graph, blob = world
        graph.apps[CONNECTOR]["status"] = "DELETING"
        assert await _run_until_idle(graph, blob) == ["idle"]
        assert blob.reads == []


class TestLoop:
    def test_indexing_starts_and_stops_the_repair_loop(self) -> None:
        import inspect

        from app import indexing_main

        source = inspect.getsource(indexing_main)
        assert source.count("run_record_label_repair_loop(app_container, graph_provider)") == 2
        assert 'getattr(app.state, "label_repair_task", None)' in source
        assert 'getattr(app.state, "label_repair_future", None)' in source

    async def test_the_loop_ends_once_every_connector_is_done(self, world, monkeypatch) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.indexing import record_label_repair as mod

        graph, blob = world
        monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(mod.MessagingUtils, "_get_redis_config", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(mod, "VectorMembershipBackfillLeaderLock", lambda *a, **k: AlwaysLeader())
        monkeypatch.setattr(
            "app.modules.transformers.blob_storage.BlobStorage", lambda *a, **k: blob,
        )
        monkeypatch.setattr(mod, "get_epoch_timestamp_in_ms", lambda: CUTOFF)
        container = MagicMock()
        container.logger.return_value = logging.getLogger("label-repair-test")

        await mod.run_record_label_repair_loop(container, graph)

        assert graph.apps[CONNECTOR][RecordLabelRepairState.STATE] == REPAIR_VERSION
        assert blob.stored["vr-b-open"]["semantic_metadata"]["topics"] == ["Shipping dates", "Product launch window"]


class TestEverySpellingAndCounters:
    async def test_two_spellings_of_one_node_both_come_back(self, world) -> None:
        graph, blob = world
        _canonical(graph, TOPICS, "t-nda", "Mutual NDA")
        _record(graph, blob, "f-two", {"topics": ["Mutual NDA"]})
        _link(graph, "f-two", BELONGS_TO_TOPIC, TOPICS, "t-nda", "NDA")
        graph.edges[(BELONGS_TO_TOPIC, f"{RECORDS}/f-two", f"{TOPICS}/t-nda")]["extractedNames"] = [
            "NDA", "Non-disclosure agreement",
        ]

        await _run_until_idle(graph, blob)

        assert blob.stored["vr-f-two"]["semantic_metadata"]["topics"] == ["NDA", "Non-disclosure agreement"]

    async def test_the_counters_describe_the_last_pass(self, world) -> None:
        graph, blob = world
        _record(graph, blob, "f-flaky", {"topics": ["Falcon launch window"]})
        _link(graph, "f-flaky", BELONGS_TO_TOPIC, TOPICS, "t-launch", "Product launch window")
        write = blob.update_record_buffer
        refused: list[str] = []

        async def _refuse_once(org_id, document_id, record_dict, virtual_record_id) -> tuple[str, int]:
            if virtual_record_id == "vr-f-flaky" and not refused:
                refused.append(virtual_record_id)
                raise RuntimeError("storage busy")
            return await write(org_id, document_id, record_dict, virtual_record_id)

        blob.update_record_buffer = _refuse_once
        await _run_until_idle(graph, blob)

        app = graph.apps[CONNECTOR]
        # Pass 1 restored b-open and failed f-flaky; pass 2 restored f-flaky only.
        assert app[RecordLabelRepairState.ATTEMPTS] == 1
        assert app[RecordLabelRepairState.FAILURES] == 0
        assert app[RecordLabelRepairState.REPAIRED] == 1
        assert app[RecordLabelRepairState.SKIPPED] == 1
        assert app[RecordLabelRepairState.EXHAUSTED] is False

    async def test_a_record_with_its_own_labels_is_left_alone(self, world) -> None:
        """Written by a resolver that keeps the record's own labels: two of its
        spellings share one node whose name differs in case, and the edge
        records only the first spelling."""
        graph, blob = world
        _canonical(graph, TOPICS, "t-bug", "Bug bash testing")
        _canonical(graph, TOPICS, "t-rel", "Release checklist")
        own = ["BUG BASH TESTING", "Bug bash testing session", "Release checklist"]
        _record(graph, blob, "g-own", {"topics": list(own), "own_labels": True})
        _link(graph, "g-own", BELONGS_TO_TOPIC, TOPICS, "t-bug", "BUG BASH TESTING ")
        _link(graph, "g-own", BELONGS_TO_TOPIC, TOPICS, "t-rel", "Release checklist")

        await _run_until_idle(graph, blob)

        assert blob.stored["vr-g-own"]["semantic_metadata"]["topics"] == own
        assert "vr-g-own" not in [w[2] for w in blob.writes]

    @pytest.mark.parametrize(
        "topics", [["Launch plan", "Project Falcon"], ["Project Falcon", "Launch plan"]],
    )
    async def test_a_node_name_among_its_own_labels_is_kept(self, world, topics) -> None:
        """The record spells the node two ways, one of which is the node's
        name, and its edge lists only the other; its labels are marked as its
        own, so neither order is touched."""
        graph, blob = world
        _canonical(graph, TOPICS, "t-plan", "Project Falcon")
        _record(graph, blob, "h-both", {"topics": list(topics), "own_labels": True})
        _link(graph, "h-both", BELONGS_TO_TOPIC, TOPICS, "t-plan", "Launch plan")
        graph.edges[(BELONGS_TO_TOPIC, f"{RECORDS}/h-both", f"{TOPICS}/t-plan")]["extractedNames"] = ["Launch plan"]

        await _run_until_idle(graph, blob)

        assert blob.stored["vr-h-both"]["semantic_metadata"]["topics"] == topics
        assert "vr-h-both" not in [w[2] for w in blob.writes]

    async def test_own_labels_in_another_order_than_the_edge_are_left_alone(self, world) -> None:
        graph, blob = world
        _canonical(graph, TOPICS, "t-nda", "Mutual NDA")
        _record(graph, blob, "i-perm", {"topics": ["Non-disclosure agreement", "NDA"], "own_labels": True})
        _link(graph, "i-perm", BELONGS_TO_TOPIC, TOPICS, "t-nda", "NDA")
        graph.edges[(BELONGS_TO_TOPIC, f"{RECORDS}/i-perm", f"{TOPICS}/t-nda")]["extractedNames"] = [
            "NDA", "Non-disclosure agreement",
        ]

        await _run_until_idle(graph, blob)

        assert blob.stored["vr-i-perm"]["semantic_metadata"]["topics"] == ["Non-disclosure agreement", "NDA"]
        assert "vr-i-perm" not in [w[2] for w in blob.writes]


# Stored labels as the earlier rewrite wrote them (each a linked node's name),
# the nodes as (key, name, the record's spelling), and the restored labels.
REWRITTEN_CASES = [
    (["Budget", "Sign off"], [("t-budget", "Budget", "Sign-off"), ("t-sign", "Sign off", "Quarterly review")],
     ["Sign-off", "Quarterly review"]),
    (["Integration testing", "end to end"],
     [("t-it", "Integration testing", "end-to-end"), ("t-e2e", "end to end", "end to end")],
     ["end-to-end", "end to end"]),
    (["Launch-plan", "Project Falcon"],
     [("t-pf", "Project Falcon", "Launch plan"), ("t-lp", "Launch-plan", "Launch-plan")],
     ["Launch-plan", "Launch plan"]),
]


class TestRewrittenLabelsWhoseSpellingsCollide:
    @pytest.mark.parametrize("reverse", [False, True])
    @pytest.mark.parametrize(("stored", "nodes", "restored"), REWRITTEN_CASES)
    async def test_each_node_name_becomes_that_nodes_spelling_in_stored_order(
        self, world, stored, nodes, restored, reverse,
    ) -> None:
        graph, blob = world
        if reverse:
            by_name = {name: spelling for _key, name, spelling in nodes}
            stored = list(reversed(stored))
            restored = [by_name[name] for name in stored]
        for key, name, _spelling in nodes:
            _canonical(graph, TOPICS, key, name)
        _record(graph, blob, "j-old", {"topics": list(stored)})
        for key, _name, spelling in nodes:
            _link(graph, "j-old", BELONGS_TO_TOPIC, TOPICS, key, spelling)

        await _run_until_idle(graph, blob)

        semantic = blob.stored["vr-j-old"]["semantic_metadata"]
        assert semantic["topics"] == restored
        assert semantic["own_labels"] is True

    async def test_a_restored_record_is_not_written_again(self, world) -> None:
        graph, blob = world
        await _run_until_idle(graph, blob)
        writes = len(blob.writes)
        reads = len(blob.reads)
        graph.apps[CONNECTOR][RecordLabelRepairState.STATE] = None

        await _run_until_idle(graph, blob)

        assert len(blob.writes) == writes
        assert blob.stored["vr-b-open"]["semantic_metadata"]["own_labels"] is True
        assert len(blob.reads) > reads

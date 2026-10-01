"""Unit tests for EntityVectorStore's deletion paths: the entityType filter
fix on ``delete_entity``, and the five-phase connector-scoped cleanup in
``delete_entities_by_connector``/``_shrink_connector_membership``.

The five phases are:
1. Scroll all entity points matching the connector.
2. Delete RECORD entities outright (single-connector by definition).
3. Delete RECORD_GROUP entities outright and collect their recordGroupIds.
4. Delete exclusive taxonomy entities (only this connectorId).
5. Strip connectorId AND collected recordGroupIds from shared taxonomy entities.
"""
from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import EntityType
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.models import ScrollResult, VectorPoint


def _make_store(vector_db_service: MagicMock | None = None) -> EntityVectorStore:
    vector_db_service = vector_db_service or MagicMock()
    vector_db_service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    if not isinstance(vector_db_service.collection_exists, AsyncMock):
        vector_db_service.collection_exists = AsyncMock(return_value=True)
    if not isinstance(vector_db_service.retrieve_points, AsyncMock):
        vector_db_service.retrieve_points = AsyncMock(return_value=[])
    store = EntityVectorStore(
        logger=MagicMock(),
        config_service=MagicMock(),
        vector_db_service=vector_db_service,
    )
    store._initialized = True  # skip embedding-model/collection bootstrap
    store._dense_embeddings = MagicMock(
        embed_documents=MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts])
    )
    store._sparse_embedder = None
    return store


def _point(
    entity_id: str,
    entity_type: str,
    connector_ids: list[str],
    record_group_ids: list[str],
    **extra,
) -> VectorPoint:
    metadata = {
        "entityId": entity_id,
        "entityType": entity_type,
        "name": extra.pop("name", "Engineering"),
        "canonicalName": extra.pop("canonicalName", "Engineering"),
        "typeCategory": extra.pop("typeCategory", "predefined"),
        "aliases": extra.pop("aliases", []),
    }
    metadata.update(extra)
    return VectorPoint(
        id=f"point-{entity_id}",
        payload={
            "metadata": metadata,
            "connectorIds": connector_ids,
            "recordGroupIds": record_group_ids,
        },
    )


def _deleted_entity_ids(vector_db_service: MagicMock) -> list[tuple[str, str]]:
    """Extract (entityType, entityId) pairs from all filter_collection calls
    that were followed by a delete_points call."""
    results = []
    for c in vector_db_service.filter_collection.call_args_list:
        must = c.kwargs.get("must", {})
        eid = must.get("metadata.entityId")
        etype = must.get("metadata.entityType")
        if eid and etype:
            results.append((etype, eid))
    return results


# ======================================================================
# TestDeleteEntityFilter — basic delete_entity filter coverage
# ======================================================================


class TestDeleteEntityFilter:
    @pytest.mark.asyncio
    async def test_filter_scopes_by_type_org_and_id(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entity("org-1", "record_group", "rg-1")

        vector_db_service.filter_collection.assert_awaited_once_with(
            must={
                "metadata.entityId": "rg-1",
                "metadata.entityType": "record_group",
                "metadata.orgId": "org-1",
            }
        )
        vector_db_service.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_failure_is_logged_not_raised(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.delete_points = AsyncMock(side_effect=RuntimeError("db down"))
        store = _make_store(vector_db_service)

        await store.delete_entity("org-1", "record", "r1")  # must not raise


# ======================================================================
# Connector deletion — against an in-memory entities collection
# ======================================================================


def _field(payload: dict, key: str):
    if key.startswith("metadata."):
        return (payload.get("metadata") or {}).get(key.split(".", 1)[1])
    return payload.get(key)


def _hits(value, wanted) -> bool:
    # Stored values are compared as strings, as Redis TAG and keyword fields do.
    values = value if isinstance(value, list) else [value]
    wants = wanted if isinstance(wanted, list) else [wanted]
    return any(str(v) in {str(w) for w in wants} for v in values if v is not None)


class _Entities:
    """The entities collection, honouring the filter shapes the store builds:
    ``must`` values match any-of (and array contains), ``must_not`` excludes."""

    def __init__(self, *points: VectorPoint) -> None:
        self.points = {p.id: p for p in points}
        self.scrolls: list[tuple[dict, str | None]] = []
        self.set_payload_calls: list[tuple[dict, dict, bool]] = []
        self.delete_calls: list[tuple[dict, bool]] = []
        self.fail_set_payload = False

    def get_capabilities(self):
        return MagicMock(supports_sparse_vectors=False)

    async def collection_exists(self, collection_name) -> bool:
        return True

    async def filter_collection(self, must=None, must_not=None, **_):
        return {"must": dict(must or {}), "must_not": dict(must_not or {})}

    def _matches(self, payload: dict, flt: dict) -> bool:
        if not all(_hits(_field(payload, k), v) for k, v in flt["must"].items()):
            return False
        return not any(_hits(_field(payload, k), v) for k, v in flt["must_not"].items())

    async def scroll(self, collection_name, scroll_filter, limit, offset=None, with_payload=None):
        self.scrolls.append((scroll_filter, offset))
        matched = sorted(
            (p for p in self.points.values() if self._matches(p.payload, scroll_filter)),
            key=lambda p: p.id,
        )
        start = int(offset or 0)
        page = matched[start:start + limit]
        more = start + limit < len(matched)
        return ScrollResult(
            points=[VectorPoint(id=p.id, payload=copy.deepcopy(p.payload)) for p in page],
            next_offset=str(start + limit) if more else None,
        )

    async def set_payload(self, collection_name, payload, filter, refresh=False):
        self.set_payload_calls.append((payload, filter, refresh))
        if self.fail_set_payload:
            raise RuntimeError("vector db down")
        for point in self.points.values():
            if self._matches(point.payload, filter):
                point.payload.update(copy.deepcopy(payload))

    async def update_payload_by_ids(self, collection_name, ids, payload):
        for point_id in ids:
            if point_id in self.points:
                self.points[point_id].payload.update(copy.deepcopy(payload))

    async def delete_points(self, collection_name, filter, refresh=False):
        self.delete_calls.append((filter, refresh))
        for point_id in [i for i, p in self.points.items() if self._matches(p.payload, filter)]:
            del self.points[point_id]


def _store_over(entities: _Entities) -> EntityVectorStore:
    store = EntityVectorStore(logger=MagicMock(), config_service=MagicMock(), vector_db_service=entities)
    store._init_embeddings = AsyncMock(side_effect=AssertionError("deletion must not embed"))
    return store


def _entity_point(entity_id, entity_type, connectors, groups, org="org-1", **meta) -> VectorPoint:
    return VectorPoint(
        id=f"{entity_type}:{entity_id}",
        payload={
            "metadata": {"entityId": entity_id, "entityType": entity_type, "orgId": org,
                         "name": meta.pop("name", entity_id), **meta},
            "connectorIds": list(connectors),
            "recordGroupIds": list(groups),
        },
    )


def _membership(entities: _Entities, point_id: str) -> tuple[list, list]:
    payload = entities.points[point_id].payload
    return payload["connectorIds"], payload["recordGroupIds"]


class TestConnectorDeletion:
    @pytest.mark.asyncio
    async def test_shared_entity_loses_the_connector_and_its_groups_without_reembedding(self) -> None:
        entities = _Entities(
            _entity_point("t1", "topic", ["conn-a", "conn-b"], ["ga", "gb"]),
            _entity_point("rg-a", "record_group", ["conn-a"], ["ga"]),
        )

        await _store_over(entities).delete_entities_by_connector("org-1", "conn-a")

        assert _membership(entities, "topic:t1") == (["conn-b"], ["gb"])
        assert "record_group:rg-a" not in entities.points
        (payload, _, refresh), = entities.set_payload_calls
        assert payload == {"connectorIds": ["conn-b"], "recordGroupIds": ["gb"]}
        assert refresh is True

    @pytest.mark.asyncio
    async def test_exclusive_taxonomy_entity_is_deleted(self) -> None:
        entities = _Entities(
            _entity_point("t1", "topic", ["conn-a"], ["ga", "g-other"]),
            _entity_point("t2", "topic", ["conn-b"], []),
        )

        await _store_over(entities).delete_entities_by_connector("org-1", "conn-a")

        assert list(entities.points) == ["topic:t2"]

    @pytest.mark.asyncio
    async def test_record_and_group_points_go_in_one_filtered_delete(self) -> None:
        """A delete per record point was one round trip per record."""
        entities = _Entities(*(
            [_entity_point(f"r{i}", "record", ["conn-a"], ["ga"]) for i in range(250)]
            + [_entity_point("rg-a", "record_group", ["conn-a"], ["ga"])]
        ))

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"],
        )

        assert entities.points == {}
        (flt, _), = entities.delete_calls
        assert flt["must"] == {"metadata.orgId": "org-1", "connectorIds": "conn-a"}

    @pytest.mark.asyncio
    async def test_entities_left_with_the_same_membership_share_one_write(self) -> None:
        entities = _Entities(
            _entity_point("t1", "topic", ["conn-a", "conn-b"], []),
            _entity_point("t2", "topic", ["conn-a", "conn-b"], []),
        )

        await _store_over(entities).delete_entities_by_connector("org-1", "conn-a")

        (payload, flt, _), = entities.set_payload_calls
        assert sorted(flt["must"]["metadata.entityId"]) == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_graph_record_groups_are_stripped_even_without_a_group_point(self) -> None:
        """Group points are best-effort (a nameless group has none), so the
        graph's list is the one that covers every group."""
        entities = _Entities(_entity_point("t1", "topic", ["conn-a", "conn-b"], ["g-nameless", "gb"]))

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["g-nameless"],
        )

        assert _membership(entities, "topic:t1") == (["conn-b"], ["gb"])

    @pytest.mark.asyncio
    async def test_without_graph_groups_record_points_supply_them(self) -> None:
        entities = _Entities(
            _entity_point("r1", "record", ["conn-a"], ["g-from-record"]),
            _entity_point("t1", "topic", ["conn-a", "conn-b"], ["g-from-record", "gb"]),
        )

        await _store_over(entities).delete_entities_by_connector("org-1", "conn-a")

        assert _membership(entities, "topic:t1") == (["conn-b"], ["gb"])

    @pytest.mark.asyncio
    async def test_with_graph_groups_record_points_are_not_scanned(self) -> None:
        entities = _Entities(_entity_point("r1", "record", ["conn-a"], ["ga"]))

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"],
        )

        group_scans = [f for f, _ in entities.scrolls if "metadata.entityType" in f["must"]]
        assert [f["must"]["metadata.entityType"] for f in group_scans] == [["record_group"]]

    @pytest.mark.asyncio
    async def test_strip_loop_rereads_from_the_start_so_it_never_pages_deep(self) -> None:
        """Processed points leave the filter, so every page is read at offset
        0; Redis refuses search offsets past 10k."""
        entities = _Entities(*(
            [_entity_point(f"t{i:03}", "topic", ["conn-a", "conn-b"], []) for i in range(120)]
            + [_entity_point(f"x{i:03}", "topic", ["conn-a"], []) for i in range(90)]
        ))

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"],
        )

        taxonomy_scans = [(f, o) for f, o in entities.scrolls if f["must_not"]]
        assert len(taxonomy_scans) >= 3
        assert all(offset is None for _, offset in taxonomy_scans)
        assert sorted(entities.points) == [f"topic:t{i:03}" for i in range(120)]
        assert all(refresh for _, refresh in entities.delete_calls[:-1])

    @pytest.mark.asyncio
    async def test_points_without_an_id_end_the_loop_and_fall_to_the_final_delete(self) -> None:
        malformed = VectorPoint(id="bad", payload={"metadata": {"orgId": "org-1"},
                                                   "connectorIds": ["conn-a", "conn-b"], "recordGroupIds": []})
        entities = _Entities(malformed)

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"],
        )

        assert entities.points == {}

    @pytest.mark.asyncio
    async def test_a_failed_strip_raises_before_the_final_delete_and_a_retry_finishes(self) -> None:
        """Deleting group points first left a retry nothing to recover the
        connector's groups from, so shared entities kept them forever."""
        entities = _Entities(
            _entity_point("t1", "topic", ["conn-a", "conn-b"], ["ga", "gb"]),
            _entity_point("rg-a", "record_group", ["conn-a"], ["ga"]),
        )
        entities.fail_set_payload = True
        store = _store_over(entities)

        with pytest.raises(RuntimeError):
            await store.delete_entities_by_connector("org-1", "conn-a")
        assert "record_group:rg-a" in entities.points

        entities.fail_set_payload = False
        await store.delete_entities_by_connector("org-1", "conn-a")

        assert _membership(entities, "topic:t1") == (["conn-b"], ["gb"])
        assert "record_group:rg-a" not in entities.points

    @pytest.mark.asyncio
    async def test_redis_type_guessed_ids_are_still_stripped(self) -> None:
        entities = _Entities(_entity_point(2024, "subcategory", ["conn-a", "conn-b"], [], level=1))

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"],
        )

        assert _membership(entities, "subcategory:2024") == (["conn-b"], [])

    @pytest.mark.asyncio
    async def test_other_orgs_are_untouched(self) -> None:
        entities = _Entities(
            _entity_point("t1", "topic", ["conn-a"], [], org="org-2"),
            _entity_point("r1", "record", ["conn-a"], [], org="org-2"),
        )

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"],
        )

        assert len(entities.points) == 2

    @pytest.mark.asyncio
    async def test_missing_collection_is_a_no_op(self) -> None:
        entities = _Entities(_entity_point("t1", "topic", ["conn-a"], []))
        entities.collection_exists = AsyncMock(return_value=False)

        await _store_over(entities).delete_entities_by_connector("org-1", "conn-a")

        assert entities.scrolls == [] and entities.delete_calls == []


class TestExclusiveLookingPointsAreCheckedAgainstTheGraph:
    """Two indexing workers can each merge against the same stale read, so a
    shared entity's point can miss a connector and look exclusive to the one
    being deleted."""

    @pytest.mark.asyncio
    async def test_a_point_other_records_still_reach_is_rewritten_not_deleted(self) -> None:
        entities = _Entities(_entity_point("t1", "topic", ["conn-a"], ["ga"]))
        lookup = AsyncMock(return_value={
            ("topic", "t1"): {"connectorIds": ["conn-b"], "recordGroupIds": ["gb"]},
        })

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"], membership_lookup=lookup,
        )

        assert _membership(entities, "topic:t1") == (["conn-b"], ["gb"])

    @pytest.mark.asyncio
    async def test_a_point_nothing_reaches_any_more_is_deleted(self) -> None:
        entities = _Entities(_entity_point("t1", "topic", ["conn-a"], []))
        lookup = AsyncMock(return_value={("topic", "t1"): {"connectorIds": [], "recordGroupIds": []}})

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"], membership_lookup=lookup,
        )

        assert entities.points == {}

    @pytest.mark.asyncio
    async def test_one_lookup_per_page_and_only_for_exclusive_points(self) -> None:
        entities = _Entities(
            _entity_point("shared", "topic", ["conn-a", "conn-b"], []),
            _entity_point("t1", "topic", ["conn-a"], []),
            _entity_point("c1", "category", ["conn-a"], []),
        )
        lookup = AsyncMock(return_value={})

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"], membership_lookup=lookup,
        )

        lookup.assert_awaited_once()
        (refs,) = lookup.await_args.args
        assert sorted((r["type"], r["id"]) for r in refs) == [("category", "c1"), ("topic", "t1")]

    @pytest.mark.asyncio
    async def test_the_deleted_connector_is_dropped_from_what_the_graph_reports(self) -> None:
        """A sync racing the deletion can still name the connector; written
        back, the point would never leave the cleanup filter."""
        entities = _Entities(_entity_point("t1", "topic", ["conn-a"], ["ga"]))
        lookup = AsyncMock(return_value={
            ("topic", "t1"): {"connectorIds": ["conn-a", "conn-b"], "recordGroupIds": ["ga", "gb"]},
        })

        await _store_over(entities).delete_entities_by_connector(
            "org-1", "conn-a", record_group_ids=["ga"], membership_lookup=lookup,
        )

        assert _membership(entities, "topic:t1") == (["conn-b"], ["gb"])

    @pytest.mark.asyncio
    async def test_a_page_that_does_not_shrink_raises_without_sweeping(self) -> None:
        """OpenSearch's update_by_query skips a point rewritten underneath it.
        The final delete would then remove a shared entity another connector
        still reaches, so the page is retried and then the cleanup raises."""
        entities = _Entities(
            _entity_point("t1", "topic", ["conn-a", "conn-b"], []),
            _entity_point("rec-1", "record", ["conn-a"], []),
        )
        writes = {"n": 0}

        async def _no_effect(collection_name, payload, filter, refresh=False):
            writes["n"] += 1

        entities.set_payload = _no_effect
        store = _store_over(entities)

        with pytest.raises(RuntimeError, match="no progress"):
            await store.delete_entities_by_connector("org-1", "conn-a", record_group_ids=[])

        assert writes["n"] == 2
        assert _membership(entities, "topic:t1") == (["conn-a", "conn-b"], [])
        assert "record:rec-1" in entities.points
        assert entities.delete_calls == []

    @pytest.mark.asyncio
    async def test_a_write_that_takes_on_the_second_attempt_completes(self) -> None:
        entities = _Entities(
            _entity_point("t1", "topic", ["conn-a", "conn-b"], ["ga", "gb"]),
            _entity_point("rec-1", "record", ["conn-a"], ["ga"]),
        )
        real_set_payload = entities.set_payload
        writes = {"n": 0}

        async def _first_skipped(collection_name, payload, filter, refresh=False) -> None:
            writes["n"] += 1
            if writes["n"] > 1:
                await real_set_payload(collection_name, payload, filter, refresh=refresh)

        entities.set_payload = _first_skipped

        await _store_over(entities).delete_entities_by_connector("org-1", "conn-a", record_group_ids=["ga"])

        assert writes["n"] == 2
        assert _membership(entities, "topic:t1") == (["conn-b"], ["gb"])
        assert "record:rec-1" not in entities.points

    @pytest.mark.asyncio
    async def test_a_full_page_of_points_without_an_id_raises_and_deletes_nothing(self) -> None:
        """Shared points may sit behind a full page that cannot be processed."""
        malformed = [
            VectorPoint(id=f"a-bad-{i}", payload={"metadata": {"orgId": "org-1"},
                                                  "connectorIds": ["conn-a"], "recordGroupIds": []})
            for i in range(2)
        ]
        entities = _Entities(*malformed, _entity_point("t1", "topic", ["conn-a", "conn-b"], []))
        store = _store_over(entities)

        with pytest.raises(RuntimeError, match="without an entity id"):
            await store._shrink_connector_membership("org-1", "conn-a", record_group_ids=[], page_size=2)

        assert _membership(entities, "topic:t1") == (["conn-a", "conn-b"], [])
        assert len(entities.points) == 3
        assert entities.delete_calls == []

    @pytest.mark.asyncio
    async def test_a_failed_lookup_changes_nothing_and_raises(self) -> None:
        entities = _Entities(_entity_point("t1", "topic", ["conn-a"], []))
        lookup = AsyncMock(side_effect=RuntimeError("graph down"))

        with pytest.raises(RuntimeError):
            await _store_over(entities).delete_entities_by_connector(
                "org-1", "conn-a", record_group_ids=["ga"], membership_lookup=lookup,
            )

        assert _membership(entities, "topic:t1") == (["conn-a"], [])


class TestUpsertEntitiesBatchMergeMembershipFlag:
    @pytest.mark.asyncio
    async def test_merge_membership_false_skips_membership_read(self) -> None:
        from app.models.entities import EntityRecord, EntityTypeCategory

        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)
        entity = EntityRecord(
            entity_id="eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            org_id="org-1",
            type_category=EntityTypeCategory.PREDEFINED,
            connector_ids=["conn-b"],
            record_group_ids=[],
        )

        await store.upsert_entities_batch([entity], merge_membership=False)

        vector_db_service.retrieve_points.assert_not_called()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-b"]

    @pytest.mark.asyncio
    async def test_merge_membership_default_true_preserves_existing_behaviour(self) -> None:
        from app.models.entities import EntityRecord, EntityTypeCategory

        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.retrieve_points = AsyncMock(
            return_value=[
                VectorPoint(
                    id=EntityVectorStore._point_id("org-1", "department", "eng"),
                    payload={
                        "metadata": {},
                        "connectorIds": ["conn-a"],
                        "recordGroupIds": [],
                    },
                )
            ]
        )
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)
        entity = EntityRecord(
            entity_id="eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            org_id="org-1",
            type_category=EntityTypeCategory.PREDEFINED,
            connector_ids=["conn-b"],
            record_group_ids=[],
        )

        await store.upsert_entities_batch([entity])

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-a", "conn-b"]

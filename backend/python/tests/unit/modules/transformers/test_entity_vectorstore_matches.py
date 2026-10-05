"""EntityVectorStore: winner lookup for resolution, level index, skip-unchanged writes."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import EntityRecord, EntityType
from app.modules.transformers.entity_vectorstore import (
    EMBEDDING_MODEL_FIELD,
    EntityVectorStore,
)
from app.services.vector_db.models import (
    FusionMethod,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorPoint,
)
from tests.support.embedding_config import config_service as embedding_config_service
from tests.support.embedding_config import skip_bootstrap


def _make_store(vector_db_service=None) -> EntityVectorStore:
    vector_db_service = vector_db_service or MagicMock()
    vector_db_service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    if not isinstance(vector_db_service.retrieve_points, AsyncMock):
        vector_db_service.retrieve_points = AsyncMock(return_value=[])
    vector_db_service.filter_collection = AsyncMock(side_effect=lambda **kw: kw)
    store = EntityVectorStore(logger=MagicMock(), config_service=embedding_config_service(), vector_db_service=vector_db_service)
    skip_bootstrap(store)
    store._model_id, store._embedding_size = "test:model", 2
    store._dense_embeddings = MagicMock(embed_documents=MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts]))
    store._dense_embeddings.embed_query = MagicMock(return_value=[0.1, 0.2])
    store._sparse_embedder = None
    return store


def _hit(entity_id, name, entity_type="topic", level=None, aliases=(), score=0.9, org_id="org-1") -> SearchResult:
    return SearchResult(
        id=f"p-{entity_id}", score=score,
        payload={"page_content": name, "metadata": {
            "entityId": entity_id, "entityType": entity_type, "name": name,
            "aliases": list(aliases), "level": level, "orgId": org_id,
        }},
    )


def _redis_shaped(payload: dict) -> dict:
    """What a Redis read hands back: every metadata value round-tripped
    through a hash string and type-guessed, so "1" is 1 and "2024" is 2024."""
    from app.services.vector_db.redis.utils import (
        _coerce_hash_value,
        _recover_typed_value,
    )

    shaped = dict(payload)
    shaped["metadata"] = {
        k: _recover_typed_value(_coerce_hash_value(v)) for k, v in payload["metadata"].items()
    }
    return shaped


class TestFindBestMatches:
    async def test_one_request_per_name_filtered_by_org_type_and_level(self) -> None:
        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[[_hit("k1", "Manual Testing", "subcategory", "2")], []])
        store = _make_store(service)
        results = await store.find_best_matches(["Integration Testing", "Other"], "org-1", "subcategory", level="2")
        assert service.filter_collection.await_args.kwargs["must"] == {
            "metadata.orgId": "org-1", "metadata.entityType": "subcategory", "metadata.level": "2",
        }
        requests = service.query_nearest_points.await_args.kwargs["requests"]
        assert [r.text_query for r in requests] == ["Integration Testing", "Other"]
        assert all(r.limit == 1 and r.fusion_method is FusionMethod.RRF for r in requests)
        assert results[0] == {
            "entityId": "k1", "entityType": "subcategory", "name": "Manual Testing",
            "aliases": [], "level": "2", "score": 0.9,
        }
        assert results[1] is None

    async def test_level_omitted_for_non_subcategory(self) -> None:
        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[[]])
        store = _make_store(service)
        await store.find_best_matches(["Legal"], "org-1", "category")
        assert "metadata.level" not in service.filter_collection.await_args.kwargs["must"]

    async def test_blank_names_and_empty_org_short_circuit(self) -> None:
        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[[_hit("k1", "x")]])
        store = _make_store(service)
        assert await store.find_best_matches(["", "  "], "org-1", "topic") == [None, None]
        assert await store.find_best_matches(["Legal"], "", "topic") == [None]
        service.query_nearest_points.assert_not_awaited()
        results = await store.find_best_matches(["", "Legal"], "org-1", "topic")
        assert results == [None, {"entityId": "k1", "entityType": "topic", "name": "x", "aliases": [], "level": None, "score": 0.9}]

    async def test_mismatched_type_or_level_is_dropped(self) -> None:
        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[
            [_hit("k1", "Legal", entity_type="category")], [_hit("k2", "Deep", "subcategory", "3")],
        ])
        store = _make_store(service)
        assert await store.find_best_matches(["a b", "c d"], "org-1", "subcategory", level="2") == [None, None]

    async def test_vector_failure_propagates(self) -> None:
        service = MagicMock()
        service.query_nearest_points = AsyncMock(side_effect=RuntimeError("down"))
        store = _make_store(service)
        with pytest.raises(RuntimeError):
            await store.find_best_matches(["Legal"], "org-1", "category")

    async def test_redis_type_guessed_level_and_name_still_match(self) -> None:
        """On Redis a level-1 subcategory came back with level 1 (an int),
        never equal to "1", so no subcategory ever found a winner."""
        hit = _hit("2024", "2024", "subcategory", "1")
        hit = SearchResult(id=hit.id, score=hit.score, payload=_redis_shaped(hit.payload))
        assert hit.payload["metadata"]["level"] == 1
        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[[hit]])
        store = _make_store(service)

        (result,) = await store.find_best_matches(["2024 plan"], "org-1", "subcategory", level="1")

        assert result is not None
        assert result["entityId"] == "2024"
        assert result["name"] == "2024"
        assert result["level"] == "1"

    async def test_hit_from_another_org_is_dropped(self) -> None:
        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[[_hit("k1", "Legal", org_id="org-2")]])
        store = _make_store(service)
        assert await store.find_best_matches(["Legal"], "org-1", "topic") == [None]


class TestLevelIndex:
    async def test_new_collection_indexes_level(self) -> None:
        service = MagicMock()
        service.get_collection_info = AsyncMock(return_value=VectorCollectionInfo(name="entities", exists=False))
        service.create_collection = AsyncMock()
        service.create_index = AsyncMock()
        store = _make_store(service)
        store._embedding_size = 2
        await store._init_collection()
        fields = [call.kwargs["field_name"] for call in service.create_index.await_args_list]
        assert "metadata.level" in fields
        assert "metadata.entityType" in fields


def _existing(entity: EntityRecord, connector_ids, record_group_ids) -> VectorPoint:
    return VectorPoint(
        id=EntityVectorStore._point_id(entity.org_id, entity.entity_type.value, entity.entity_id),
        payload={
            "page_content": entity.embedding_text,
            # Embedded by the store's own model, so only content decides.
            "metadata": {**entity.to_vector_payload(), EMBEDDING_MODEL_FIELD: "test:model:2"},
            "connectorIds": list(connector_ids),
            "recordGroupIds": list(record_group_ids),
        },
    )


class TestSkipUnchanged:
    def _entity(self, **kwargs) -> EntityRecord:
        base = {"entity_id": "k1", "entity_type": EntityType.TOPIC, "name": "Bug bash testing", "org_id": "org-1"}
        base.update(kwargs)
        return EntityRecord(**base)

    async def test_identical_point_skips_embedding_and_upsert(self) -> None:
        service = MagicMock()
        entity = self._entity(connector_ids=["c1"])
        service.retrieve_points = AsyncMock(return_value=[_existing(entity, ["c1"], [])])
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([entity])
        service.upsert_points.assert_not_awaited()
        store._dense_embeddings.embed_documents.assert_not_called()

    async def test_new_alias_rewrites_the_point(self) -> None:
        service = MagicMock()
        stored = self._entity(connector_ids=["c1"])
        service.retrieve_points = AsyncMock(return_value=[_existing(stored, ["c1"], [])])
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([self._entity(connector_ids=["c1"], aliases=["bbt"])])
        service.upsert_points.assert_awaited_once()
        (point,) = service.upsert_points.await_args.kwargs["points"]
        # The alias lands in the payload only; the embedded text is the name.
        assert point.payload["page_content"] == "Bug bash testing"
        assert point.payload["metadata"]["aliases"] == ["bbt"]
        store._dense_embeddings.embed_documents.assert_called_once_with(["Bug bash testing"])

    async def test_new_membership_alone_is_written_without_reembedding(self) -> None:
        """The stored vector is still right; only the arrays move."""
        service = MagicMock()
        stored = self._entity(connector_ids=["c1"])
        service.retrieve_points = AsyncMock(return_value=[_existing(stored, ["c1"], [])])
        service.upsert_points = AsyncMock()
        service.update_payload_by_ids = AsyncMock()
        store = _make_store(service)

        await store.upsert_entities_batch([self._entity(connector_ids=["c2"])])

        service.upsert_points.assert_not_awaited()
        store._dense_embeddings.embed_documents.assert_not_called()
        # By id, not by filter: a search-based update cannot see a point the
        # index has not refreshed yet.
        collection, ids, payload = service.update_payload_by_ids.await_args.args
        assert ids == [EntityVectorStore._point_id("org-1", "topic", "k1")]
        assert payload == {"connectorIds": ["c1", "c2"], "recordGroupIds": []}

    async def test_text_changes_are_embedded_and_membership_changes_are_not(self) -> None:
        service = MagicMock()
        same_text = self._entity(entity_id="same", connector_ids=["c1"])
        renamed = self._entity(entity_id="renamed", name="Old name", connector_ids=["c1"])
        service.retrieve_points = AsyncMock(return_value=[
            _existing(same_text, ["c1"], []), _existing(renamed, ["c1"], []),
        ])
        service.upsert_points = AsyncMock()
        service.update_payload_by_ids = AsyncMock()
        store = _make_store(service)

        await store.upsert_entities_batch([
            self._entity(entity_id="same", connector_ids=["c2"]),
            self._entity(entity_id="renamed", name="New name", connector_ids=["c1"]),
        ])

        store._dense_embeddings.embed_documents.assert_called_once_with(["New name"])
        service.update_payload_by_ids.assert_awaited_once()

    async def test_replace_mode_skips_an_unchanged_point(self) -> None:
        """Record and record-group points are written on every indexed record;
        rewriting an unchanged one re-embedded it each time."""
        service = MagicMock()
        stored = self._entity(connector_ids=["c1"])
        service.retrieve_points = AsyncMock(return_value=[_existing(stored, ["c1"], [])])
        service.upsert_points = AsyncMock()
        service.update_payload_by_ids = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([stored], merge_membership=False)
        service.upsert_points.assert_not_awaited()
        service.update_payload_by_ids.assert_not_awaited()

    async def test_read_failure_skips_the_write(self) -> None:
        """Writing blind would replace the stored membership with only this
        caller's ids — the deterministic point ID makes it an overwrite."""
        service = MagicMock()
        service.retrieve_points = AsyncMock(side_effect=RuntimeError("down"))
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([self._entity()])
        service.upsert_points.assert_not_awaited()

    async def test_only_changed_entities_in_a_batch_are_embedded(self) -> None:
        service = MagicMock()
        unchanged = self._entity(entity_id="same", connector_ids=["c1"])
        changed = self._entity(entity_id="changed", connector_ids=["c1"])
        service.retrieve_points = AsyncMock(return_value=[_existing(unchanged, ["c1"], [])])
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([unchanged, changed])
        store._dense_embeddings.embed_documents.assert_called_once_with([changed.embedding_text])
        (point,) = service.upsert_points.await_args.kwargs["points"]
        assert point.payload["metadata"]["entityId"] == "changed"


class TestRedisShapedMembershipRead:
    def _entity(self, **kwargs) -> EntityRecord:
        base = {
            "entity_id": "2024", "entity_type": EntityType.SUBCATEGORY, "name": "2024",
            "org_id": "org-1", "level": "1", "connector_ids": ["c1"],
        }
        base.update(kwargs)
        return EntityRecord(**base)

    async def test_unchanged_point_read_back_from_redis_is_not_rewritten(self) -> None:
        """Type-guessed metadata never equalled the entity's own payload, so
        every write re-embedded and re-upserted an unchanged point."""
        entity = self._entity()
        stored = _existing(entity, ["c1"], [])
        stored = VectorPoint(id=stored.id, payload=_redis_shaped(stored.payload))
        service = MagicMock()
        service.retrieve_points = AsyncMock(return_value=[stored])
        service.upsert_points = AsyncMock()
        store = _make_store(service)

        await store.upsert_entities_batch([entity])

        service.upsert_points.assert_not_awaited()
        store._dense_embeddings.embed_documents.assert_not_called()


class TestMembershipReadIsByPointId:
    def _entity(self, entity_id, **kwargs) -> EntityRecord:
        return EntityRecord(
            entity_id=entity_id, entity_type=EntityType.TOPIC, name=f"name {entity_id}",
            org_id="org-1", **kwargs,
        )

    async def test_one_read_per_batch_by_deterministic_id_and_no_search(self) -> None:
        service = MagicMock()
        service.scroll = AsyncMock()
        service.upsert_points = AsyncMock()
        store = _make_store(service)

        await store.upsert_entities_batch([self._entity("a"), self._entity("b")])

        # One read before embedding, without the locks, and the
        # authoritative one under them; both by id, never a search.
        assert service.retrieve_points.await_count == 2
        expected = [
            EntityVectorStore._point_id("org-1", "topic", "a"),
            EntityVectorStore._point_id("org-1", "topic", "b"),
        ]
        for call in service.retrieve_points.await_args_list:
            assert call.args == (store.collection_name, expected)
        service.scroll.assert_not_called()

    async def test_a_write_not_yet_searchable_is_still_merged(self) -> None:
        """OpenSearch search lags writes by up to 30s; reading by id sees the
        first writer's connector, so the second writer keeps it."""
        stored: dict[str, VectorPoint] = {}
        service = MagicMock()
        service.scroll = AsyncMock(return_value=ScrollResult(points=[], next_offset=None))

        async def _retrieve(collection_name, ids):
            return [stored[i] for i in ids if i in stored]

        async def _upsert(collection_name, points):
            for point in points:
                stored[point.id] = point

        async def _update_by_ids(collection_name, ids, payload):
            for point_id in ids:
                if point_id in stored:
                    stored[point_id].payload.update(payload)

        service.retrieve_points = AsyncMock(side_effect=_retrieve)
        service.upsert_points = AsyncMock(side_effect=_upsert)
        service.update_payload_by_ids = AsyncMock(side_effect=_update_by_ids)
        store = _make_store(service)

        await store.upsert_entities_batch([self._entity("eng", connector_ids=["drive"])])
        await store.upsert_entities_batch([self._entity("eng", connector_ids=["jira"])])

        (point,) = stored.values()
        assert point.payload["connectorIds"] == ["drive", "jira"]


class TestDeletesNeedNoEmbeddings:
    def _store(self, service) -> EntityVectorStore:
        service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
        store = EntityVectorStore(logger=MagicMock(), config_service=embedding_config_service(), vector_db_service=service)
        store._init_embeddings = AsyncMock(side_effect=RuntimeError("embedding endpoint down"))
        return store

    async def test_delete_entity_works_while_embeddings_are_down(self) -> None:
        service = MagicMock()
        service.collection_exists = AsyncMock(return_value=True)
        service.filter_collection = AsyncMock(return_value={"must": []})
        service.delete_points = AsyncMock()
        store = self._store(service)

        await store.delete_entity("org-1", "record", "r1")

        service.delete_points.assert_awaited_once()
        store._init_embeddings.assert_not_called()

    async def test_delete_entity_with_no_collection_is_a_no_op(self) -> None:
        service = MagicMock()
        service.collection_exists = AsyncMock(return_value=False)
        service.delete_points = AsyncMock()
        store = self._store(service)

        await store.delete_entity("org-1", "record", "r1")

        service.delete_points.assert_not_called()

    async def test_delete_entity_never_raises(self) -> None:
        service = MagicMock()
        service.collection_exists = AsyncMock(side_effect=RuntimeError("vector db down"))
        store = self._store(service)

        await store.delete_entity("org-1", "record", "r1")

    async def test_connector_and_org_deletes_do_not_initialise_embeddings(self) -> None:
        service = MagicMock()
        service.collection_exists = AsyncMock(return_value=True)
        service.filter_collection = AsyncMock(return_value={"must": []})
        service.scroll = AsyncMock(return_value=ScrollResult(points=[], next_offset=None))
        service.delete_points = AsyncMock()
        store = self._store(service)

        await store.delete_entities_by_connector("org-1", "conn-1")
        await store.delete_entities_for_org("org-1")

        store._init_embeddings.assert_not_called()
        # The connector's final filtered delete, then the org delete.
        assert service.delete_points.await_count == 2


class TestInitHousekeeping:
    async def test_existing_collection_still_gets_its_payload_indexes(self) -> None:
        """A process that died between creating the collection and indexing it
        left the collection without indexes for good."""
        service = MagicMock()
        service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="entities", exists=True, dense_dimension=2)
        )
        service.create_collection = AsyncMock()
        service.create_index = AsyncMock()
        service.scroll = AsyncMock(return_value=ScrollResult(points=[]))
        store = _make_store(service)
        store._embedding_size = 2

        await store._init_collection()

        service.create_collection.assert_not_awaited()
        fields = {c.kwargs["field_name"] for c in service.create_index.await_args_list}
        assert {"metadata.orgId", "metadata.level", "connectorIds", "recordGroupIds"} <= fields

    async def test_missing_ai_models_config_uses_the_default_model(self, monkeypatch) -> None:
        from app.modules.transformers import entity_vectorstore as module

        default = MagicMock(embed_query=MagicMock(return_value=[0.1, 0.2]))
        monkeypatch.setattr(module, "get_default_embedding_model", lambda: default)
        service = MagicMock()
        service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
        store = EntityVectorStore(logger=MagicMock(), config_service=embedding_config_service(), vector_db_service=service)
        store._init_collection = AsyncMock()

        await store._ensure_initialized()

        assert store._dense_embeddings is default

    async def test_one_query_is_embedded_once_across_search_passes(self) -> None:
        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[[]])
        store = _make_store(service)

        for _ in range(3):
            await store.search_entities(
                "legal", "org-1", {"rg-1"}, set(),
            )

        store._dense_embeddings.embed_query.assert_called_once_with("legal")


class TestInitialisationBackoff:
    async def test_a_failed_init_is_not_retried_until_the_cooldown_passes(self, monkeypatch) -> None:
        from app.exceptions.indexing_exceptions import VectorStoreError
        from app.modules.transformers import entity_vectorstore as module

        clock = {"now": 1000.0}
        monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
        service = MagicMock()
        service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
        store = EntityVectorStore(logger=MagicMock(), config_service=embedding_config_service(), vector_db_service=service)
        store._init_embeddings = AsyncMock(side_effect=RuntimeError("embedding endpoint down"))
        store._init_collection = AsyncMock()

        with pytest.raises(RuntimeError):
            await store._ensure_initialized()
        with pytest.raises(VectorStoreError):
            await store._ensure_initialized()
        assert store._init_embeddings.await_count == 1

        clock["now"] += module._INIT_RETRY_SECONDS + 1
        store._init_embeddings.side_effect = None
        await store._ensure_initialized()

        assert store._init_embeddings.await_count == 2
        assert store._initialized is True


class TestSearchPassesAreOneRequest:
    """KG-08: every pass of an entity search goes to the vector DB at once."""

    async def test_one_request_per_pass_in_a_single_call(self) -> None:
        from app.modules.transformers.entity_vectorstore import EntitySearchPass
        from app.services.vector_db.models import SearchResult

        service = MagicMock()
        service.query_nearest_points = AsyncMock(return_value=[
            [SearchResult(id="p1", score=0.9, payload={"metadata": {"entityId": "a", "entityType": "topic"}})],
            [SearchResult(id="p2", score=0.8, payload={"metadata": {"entityId": "b", "entityType": "topic"}})],
        ])
        store = _make_store(service)

        results = await store.search_entities_passes("q", "org-1", [
            EntitySearchPass(frozenset({"g1"}), frozenset({"c1"})),
            EntitySearchPass(frozenset(), frozenset()),  # no scope, not org-wide: skipped
            EntitySearchPass(org_wide=True),
        ])

        service.query_nearest_points.assert_awaited_once()
        requests = service.query_nearest_points.await_args.kwargs["requests"]
        assert len(requests) == 2
        assert requests[0].filter["should"] == {"recordGroupIds": ["g1"], "connectorIds": ["c1"]}
        assert requests[1].filter["should"] == {}
        assert [[h["entityId"] for h in r] for r in results] == [["a"], [], ["b"]]

    async def test_no_searchable_pass_makes_no_request(self) -> None:
        from app.modules.transformers.entity_vectorstore import EntitySearchPass

        service = MagicMock()
        service.query_nearest_points = AsyncMock()
        store = _make_store(service)
        assert await store.search_entities_passes("q", "org-1", [EntitySearchPass()]) == [[]]
        service.query_nearest_points.assert_not_awaited()

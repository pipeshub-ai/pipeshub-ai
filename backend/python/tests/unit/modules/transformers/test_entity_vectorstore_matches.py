"""EntityVectorStore: winner lookup for resolution, level index, skip-unchanged writes."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import EntityRecord, EntityType
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.models import (
    FusionMethod,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorPoint,
)


def _make_store(vector_db_service=None) -> EntityVectorStore:
    vector_db_service = vector_db_service or MagicMock()
    vector_db_service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    vector_db_service.filter_collection = AsyncMock(side_effect=lambda **kw: kw)
    store = EntityVectorStore(logger=MagicMock(), config_service=MagicMock(), vector_db_service=vector_db_service)
    store._initialized = True
    store._dense_embeddings = MagicMock(embed_documents=MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts]))
    store._dense_embeddings.embed_query = MagicMock(return_value=[0.1, 0.2])
    store._sparse_embedder = None
    return store


def _hit(entity_id, name, entity_type="topic", level=None, aliases=(), score=0.9) -> SearchResult:
    return SearchResult(
        id=f"p-{entity_id}", score=score,
        payload={"page_content": name, "metadata": {
            "entityId": entity_id, "entityType": entity_type, "name": name,
            "aliases": list(aliases), "level": level,
        }},
    )


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
            "metadata": entity.to_vector_payload(),
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
        service.scroll = AsyncMock(return_value=ScrollResult(points=[_existing(entity, ["c1"], [])], next_offset=None))
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([entity])
        service.upsert_points.assert_not_awaited()
        store._dense_embeddings.embed_documents.assert_not_called()

    async def test_new_alias_rewrites_the_point(self) -> None:
        service = MagicMock()
        stored = self._entity(connector_ids=["c1"])
        service.scroll = AsyncMock(return_value=ScrollResult(points=[_existing(stored, ["c1"], [])], next_offset=None))
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([self._entity(connector_ids=["c1"], aliases=["bbt"])])
        service.upsert_points.assert_awaited_once()
        (point,) = service.upsert_points.await_args.kwargs["points"]
        # The alias lands in the payload only; the embedded text is the name.
        assert point.payload["page_content"] == "Bug bash testing"
        assert point.payload["metadata"]["aliases"] == ["bbt"]
        store._dense_embeddings.embed_documents.assert_called_once_with(["Bug bash testing"])

    async def test_new_membership_rewrites_and_unions(self) -> None:
        service = MagicMock()
        stored = self._entity(connector_ids=["c1"])
        service.scroll = AsyncMock(return_value=ScrollResult(points=[_existing(stored, ["c1"], [])], next_offset=None))
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([self._entity(connector_ids=["c2"])])
        (point,) = service.upsert_points.await_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["c1", "c2"]

    async def test_replace_mode_always_writes(self) -> None:
        service = MagicMock()
        stored = self._entity(connector_ids=["c1"])
        service.scroll = AsyncMock(return_value=ScrollResult(points=[_existing(stored, ["c1"], [])], next_offset=None))
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([stored], merge_membership=False)
        service.upsert_points.assert_awaited_once()

    async def test_read_failure_still_writes(self) -> None:
        service = MagicMock()
        service.scroll = AsyncMock(side_effect=RuntimeError("down"))
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([self._entity()])
        service.upsert_points.assert_awaited_once()

    async def test_only_changed_entities_in_a_batch_are_embedded(self) -> None:
        service = MagicMock()
        unchanged = self._entity(entity_id="same", connector_ids=["c1"])
        changed = self._entity(entity_id="changed", connector_ids=["c1"])

        async def _scroll(collection_name, scroll_filter, limit, offset=None) -> ScrollResult:
            wanted = scroll_filter["must"]["metadata.entityId"]
            if wanted == "same":
                return ScrollResult(points=[_existing(unchanged, ["c1"], [])], next_offset=None)
            return ScrollResult(points=[], next_offset=None)

        service.scroll = AsyncMock(side_effect=_scroll)
        service.upsert_points = AsyncMock()
        store = _make_store(service)
        await store.upsert_entities_batch([unchanged, changed])
        store._dense_embeddings.embed_documents.assert_called_once_with([changed.embedding_text])
        (point,) = service.upsert_points.await_args.kwargs["points"]
        assert point.payload["metadata"]["entityId"] == "changed"

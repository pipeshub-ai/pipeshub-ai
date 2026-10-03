"""What the entity index rebuild needs from EntityVectorStore: a forced
rewrite, a count of what was not written, a raising delete, paging of an
org's points, the embedding fingerprint, and recreating the collection when
the model's dimension changed."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import VectorStoreError
from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers.entity_vectorstore import (
    EMBEDDING_MODEL_FIELD,
    EntityPointRef,
    EntityVectorStore,
)
from app.services.vector_db.models import (
    ScrollResult,
    VectorCollectionInfo,
    VectorPoint,
)

ORG = "org-1"


class _StatefulVectorDB:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.upserts: list[list[VectorPoint]] = []
        self.fail_upserts = False
        self.fail_reads = False

    def get_capabilities(self) -> MagicMock:
        return MagicMock(supports_sparse_vectors=False)

    async def retrieve_points(self, collection: str, ids: list[str]) -> list[VectorPoint]:
        if self.fail_reads:
            raise RuntimeError("vector db down")
        return [VectorPoint(id=i, payload=dict(self.points[i])) for i in ids if i in self.points]

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        if self.fail_upserts:
            raise RuntimeError("write failed")
        self.upserts.append(list(points))
        for point in points:
            self.points[point.id] = dict(point.payload)

    async def update_payload_by_ids(self, collection: str, ids: list[str], payload: dict) -> None:
        for point_id in ids:
            self.points[point_id].update(payload)


def _store(db: _StatefulVectorDB | MagicMock, **kwargs: bool) -> tuple[EntityVectorStore, MagicMock]:
    store = EntityVectorStore(
        logger=logging.getLogger("entity-store-test"),
        config_service=MagicMock(),
        vector_db_service=db,
        **kwargs,
    )
    store._initialized = True
    store._model_id, store._embedding_size = "openAI:text-embedding-3-small", 2
    embed = MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts])
    store._dense_embeddings = MagicMock(embed_documents=embed)
    store._sparse_embedder = None
    return store, embed


def _topic(entity_id: str = "t1", name: str = "Pricing") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id, entity_type=EntityType.TOPIC, name=name, org_id=ORG,
        connector_ids=["c1"], type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
    )


class TestEmbeddingModelOnPoints:
    """Each point records the model that embedded it; a point from another
    model, or from before this was recorded, is re-embedded on its next write."""

    async def test_point_records_the_model(self) -> None:
        db = _StatefulVectorDB()
        store, _ = _store(db)
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        (payload,) = db.points.values()
        assert payload["metadata"][EMBEDDING_MODEL_FIELD] == store._fingerprint()

    async def test_same_model_unchanged_point_is_skipped(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        assert embed.call_count == 1

    async def test_point_from_another_model_is_reembedded(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        store._model_id = "openAI:text-embedding-3-large"
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        assert embed.call_count == 2
        (payload,) = db.points.values()
        assert payload["metadata"][EMBEDDING_MODEL_FIELD] == store._fingerprint()

    async def test_point_without_a_recorded_model_is_reembedded(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        (payload,) = db.points.values()
        del payload["metadata"][EMBEDDING_MODEL_FIELD]
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        assert embed.call_count == 2

    async def test_membership_only_change_keeps_the_recorded_model(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_topic()])
        await store.upsert_entities_batch([_topic().model_copy(update={"connector_ids": ["c2"]})])
        assert embed.call_count == 1
        (payload,) = db.points.values()
        assert payload["connectorIds"] == ["c1", "c2"]
        assert payload["metadata"][EMBEDDING_MODEL_FIELD] == store._fingerprint()


class TestFailureCount:
    async def test_clean_write_reports_no_failures(self) -> None:
        store, _ = _store(_StatefulVectorDB())
        assert (await store.upsert_entities_batch([_topic("t1"), _topic("t2")])).failed == 0

    async def test_failed_write_counts_every_entity_in_the_batch(self) -> None:
        db = _StatefulVectorDB()
        db.fail_upserts = True
        store, _ = _store(db)
        entities = [_topic(f"t{i}") for i in range(5)]
        assert (await store.upsert_entities_batch(entities, batch_size=2)).failed == 5

    async def test_skipped_merge_on_unknown_membership_counts_as_failed(self) -> None:
        db = _StatefulVectorDB()
        db.fail_reads = True
        store, _ = _store(db)
        assert (await store.upsert_entities_batch([_topic()])).failed == 1

    async def test_empty_name_is_skipped_not_failed(self) -> None:
        store, _ = _store(_StatefulVectorDB())
        outcome = await store.upsert_entities_batch([_topic(name="  ")])
        assert (outcome.failed, outcome.skipped) == (0, 1)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    db.collection_exists = AsyncMock(return_value=True)
    db.filter_collection = AsyncMock(side_effect=lambda **kw: kw)
    db.delete_points = AsyncMock()
    return db


class TestDeleteEntities:
    async def test_deletes_by_org_type_and_ids(self) -> None:
        db = _mock_db()
        store, _ = _store(db)
        await store.delete_entities(ORG, "topic", ["t1", "t2"])
        expr = db.delete_points.await_args.args[1]
        assert expr == {"must": {
            "metadata.orgId": ORG, "metadata.entityType": "topic", "metadata.entityId": ["t1", "t2"],
        }}

    @pytest.mark.parametrize(
        ("org", "ids"), [(ORG, []), ("", ["t1"]), (ORG, ["", None])],
        ids=["no-ids", "no-org", "blank-ids"],
    )
    async def test_never_widens_to_an_empty_filter(self, org: str, ids: list) -> None:
        """Providers drop empty filter values, which would widen the delete."""
        db = _mock_db()
        store, _ = _store(db)
        await store.delete_entities(org, "topic", ids)
        db.delete_points.assert_not_awaited()

    async def test_failure_raises(self) -> None:
        db = _mock_db()
        db.delete_points = AsyncMock(side_effect=RuntimeError("down"))
        store, _ = _store(db)
        with pytest.raises(RuntimeError):
            await store.delete_entities(ORG, "topic", ["t1"])

    async def test_missing_collection_is_a_noop(self) -> None:
        db = _mock_db()
        db.collection_exists = AsyncMock(return_value=False)
        store, _ = _store(db)
        await store.delete_entities(ORG, "topic", ["t1"])
        db.delete_points.assert_not_awaited()


class TestPageEntityPoints:
    async def test_pages_an_orgs_points_of_the_given_types(self) -> None:
        db = _mock_db()
        db.scroll = AsyncMock(return_value=ScrollResult(
            points=[
                VectorPoint(id="p1", payload={"metadata": {"entityType": "topic", "entityId": "t1"}}),
                VectorPoint(id="p2", payload={"metadata": {
                    "entityType": "subcategory", "entityId": "s1", "level": 2,
                }}),
                VectorPoint(id="p3", payload={"metadata": {"entityType": "topic"}}),
            ],
            next_offset="p4",
        ))
        store, _ = _store(db)
        refs, offset = await store.page_entity_points(
            ORG, ["topic", "subcategory"], offset="p1", limit=3,
        )
        assert refs == [
            EntityPointRef("topic", "t1", None),
            EntityPointRef("subcategory", "s1", "2"),
        ]
        assert offset == "p4"
        kwargs = db.scroll.await_args.kwargs
        assert kwargs["scroll_filter"] == {"must": {
            "metadata.orgId": ORG, "metadata.entityType": ["topic", "subcategory"],
        }}
        assert kwargs["offset"] == "p1" and kwargs["limit"] == 3
        assert "page_content" not in kwargs["with_payload"]

    async def test_missing_collection_has_no_points(self) -> None:
        db = _mock_db()
        db.collection_exists = AsyncMock(return_value=False)
        db.scroll = AsyncMock()
        store, _ = _store(db)
        assert await store.page_entity_points(ORG, ["topic"]) == ([], None)
        db.scroll.assert_not_awaited()

    async def test_requires_an_org(self) -> None:
        store, _ = _store(_mock_db())
        with pytest.raises(ValueError):
            await store.page_entity_points("", ["topic"])


# ---------------------------------------------------------------------------
# Initialisation: fingerprint and dimension change
# ---------------------------------------------------------------------------


def _init_store(
    existing_dim: int | None, model_dim: int, *, recreate: bool,
    embedding_config: dict | None = None,
) -> tuple[EntityVectorStore, MagicMock, MagicMock]:
    db = MagicMock()
    db.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    db.get_collection_info = AsyncMock(return_value=VectorCollectionInfo(
        name="entities", exists=existing_dim is not None, dense_dimension=existing_dim,
    ))
    db.create_collection = AsyncMock()
    db.delete_collection = AsyncMock()
    db.create_index = AsyncMock()
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"embedding": [embedding_config]} if embedding_config else {})
    store = EntityVectorStore(
        logger=logging.getLogger("entity-store-test"), config_service=config,
        vector_db_service=db, recreate_on_dimension_mismatch=recreate,
    )
    model = MagicMock(embed_query=MagicMock(return_value=[0.0] * model_dim))
    return store, db, model


OPENAI = {"provider": "openAI", "isDefault": True, "configuration": {"model": "text-embedding-3-small, other"}}


class TestDimensionChange:
    async def test_mismatch_raises_by_default(self) -> None:
        store, db, model = _init_store(768, 1536, recreate=False, embedding_config=OPENAI)
        with patch("app.modules.transformers.entity_vectorstore.get_embedding_model", return_value=model), \
             pytest.raises(VectorStoreError):
            await store._ensure_initialized()
        db.delete_collection.assert_not_awaited()

    async def test_mismatch_recreates_when_enabled(self, caplog) -> None:
        store, db, model = _init_store(768, 1536, recreate=True, embedding_config=OPENAI)
        with patch("app.modules.transformers.entity_vectorstore.get_embedding_model", return_value=model), \
             caplog.at_level(logging.WARNING, logger="entity-store-test"):
            await store._ensure_initialized()
        db.delete_collection.assert_awaited_once()
        assert db.create_collection.await_args.kwargs["config"].embedding_size == 1536
        assert any("768" in r.getMessage() and "1536" in r.getMessage() for r in caplog.records)

    async def test_matching_dimension_is_left_alone(self) -> None:
        store, db, model = _init_store(1536, 1536, recreate=True, embedding_config=OPENAI)
        with patch("app.modules.transformers.entity_vectorstore.get_embedding_model", return_value=model):
            await store._ensure_initialized()
        db.delete_collection.assert_not_awaited()
        db.create_collection.assert_not_awaited()


class TestFingerprint:
    async def test_configured_model_fingerprint(self) -> None:
        store, _, model = _init_store(1536, 1536, recreate=False, embedding_config=OPENAI)
        with patch("app.modules.transformers.entity_vectorstore.get_embedding_model", return_value=model):
            assert await store.embedding_fingerprint() == "openAI:text-embedding-3-small:1536"

    async def test_default_model_fingerprint(self) -> None:
        store, _, model = _init_store(384, 384, recreate=False)
        with patch("app.modules.transformers.entity_vectorstore.get_default_embedding_model", return_value=model):
            fingerprint = await store.embedding_fingerprint()
        assert fingerprint.startswith("default:") and fingerprint.endswith(":384")

    async def test_fingerprint_changes_with_the_model(self) -> None:
        other = {**OPENAI, "configuration": {"model": "text-embedding-3-large"}}
        a, _, model_a = _init_store(1536, 1536, recreate=False, embedding_config=OPENAI)
        b, _, model_b = _init_store(1536, 1536, recreate=False, embedding_config=other)
        with patch("app.modules.transformers.entity_vectorstore.get_embedding_model", return_value=model_a):
            fa = await a.embedding_fingerprint()
        with patch("app.modules.transformers.entity_vectorstore.get_embedding_model", return_value=model_b):
            fb = await b.embedding_fingerprint()
        assert fa != fb


class TestARecreatedCollectionReachesRunningServices:
    """The indexing service recreates the collection when the model's
    dimension changes. A query or connector service initialised before keeps
    the old model; its searches fail until it re-initialises."""

    @staticmethod
    def _searching_store(collection_dimension: int) -> tuple[EntityVectorStore, MagicMock]:
        db = MagicMock()
        db.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
        db.filter_collection = AsyncMock(return_value={})
        db.query_nearest_points = AsyncMock(side_effect=RuntimeError("Vector dimension error"))
        db.get_collection_info = AsyncMock(return_value=VectorCollectionInfo(
            name="entities", exists=True, dense_dimension=collection_dimension,
        ))
        store, _ = _store(db)
        store._dense_embeddings.embed_query = MagicMock(return_value=[0.1, 0.2])
        store._query_vector_cache["pricing"] = ([0.1, 0.2], None)
        return store, db

    async def test_a_search_failing_on_a_recreated_collection_resets_the_store(self) -> None:
        store, _ = self._searching_store(collection_dimension=4)
        with pytest.raises(RuntimeError):
            await store.search_entities("pricing", ORG, set(), {"c1"})
        assert store._initialized is False
        assert not store._query_vector_cache

    async def test_a_matching_failure_with_the_dimension_unchanged_keeps_it(self) -> None:
        store, _ = self._searching_store(collection_dimension=2)
        with pytest.raises(RuntimeError):
            await store.search_entities("pricing", ORG, set(), {"c1"})
        assert store._initialized is True

    async def test_the_merge_candidate_search_resets_it_too(self) -> None:
        store, _ = self._searching_store(collection_dimension=4)
        with pytest.raises(RuntimeError):
            await store.find_best_matches(["Pricing"], ORG, "topic")
        assert store._initialized is False

    async def test_after_the_reset_the_next_call_reads_the_new_model(self) -> None:
        store, db = self._searching_store(collection_dimension=4)
        with pytest.raises(RuntimeError):
            await store.search_entities("pricing", ORG, set(), {"c1"})

        async def _new_model() -> None:
            store._embedding_size = 4
            store._dense_embeddings.embed_query = MagicMock(return_value=[0.1] * 4)

        db.query_nearest_points = AsyncMock(return_value=[[]])
        db.create_index = AsyncMock()
        with patch.object(store, "_init_embeddings", side_effect=_new_model) as reinit:
            assert await store.search_entities("pricing", ORG, set(), {"c1"}) == []
        reinit.assert_awaited_once()
        request = db.query_nearest_points.await_args.kwargs["requests"][0]
        assert len(request.dense_query) == 4

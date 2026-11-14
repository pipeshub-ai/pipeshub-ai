"""The entity index and the weight precision: its fingerprint, its tags, and the flip that must not be swallowed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.indexing_exceptions import EmbeddingModelUnavailableError
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.utils.embedding_server_client import EmbeddingServerEmbeddings


class _Bundled(EmbeddingServerEmbeddings):
    def __init__(self, intent: str | None) -> None:
        self.model = "m"
        self.intent = intent
        self.read_ok = True

    async def _read_precision_once(self) -> bool:
        return self.read_ok

    def embed_query(self, text):  # noqa: ANN202
        return [0.0, 0.0, 0.0]

    def embed_documents_witnessed(self, texts):  # noqa: ANN202
        return [[0.1] * 3 for _ in texts], "int8-w", False


def _store(embedder, per_org: bool = False) -> EntityVectorStore:
    store = object.__new__(EntityVectorStore)
    store._dense_embeddings = embedder
    store._model_id = "provider:model"
    store._embedding_size = 3
    store._capabilities = SimpleNamespace(spaces_per_org=per_org)
    store._entity_witness = {}
    return store


class TestFingerprint:
    def test_fp32_and_providers_without_a_precision_keep_their_fingerprint(self) -> None:
        for embedder in (_Bundled("fp32"), _Bundled(None), object(), MagicMock()):
            assert _store(embedder)._fingerprint() == "provider:model:3"

    def test_an_int8_setting_adds_a_suffix(self) -> None:
        assert _store(_Bundled("int8-w"))._fingerprint() == "provider:model:3@int8-w"

    def test_a_fallback_boot_does_not_change_it(self) -> None:
        bundled = _Bundled("int8-w")
        bundled.precision = "fp32"  # what happens to serve is not part of it
        assert _store(bundled)._fingerprint() == "provider:model:3@int8-w"

    def test_a_provider_with_one_space_per_org_keeps_its_identity(self) -> None:
        assert _store(_Bundled("int8-w"), per_org=True)._fingerprint() == "provider:model:3"

    @pytest.mark.asyncio
    async def test_an_unreadable_setting_is_retried_never_guessed(self) -> None:
        bundled = _Bundled("int8-w")
        bundled.read_ok = False
        with pytest.raises(EmbeddingModelUnavailableError):
            await _store(bundled)._embedding_dimension()


class TestWitnessedEmbedding:
    @pytest.mark.asyncio
    async def test_the_bundled_server_names_the_weights_and_others_do_not(self) -> None:
        vectors, witnessed = await _store(_Bundled("int8-w"))._embed_witnessed(["a"])
        assert vectors == [[0.1] * 3] and witnessed == ("int8-w", False)
        other = MagicMock()
        other.embed_documents = MagicMock(return_value=[[0.2] * 3])
        _, witnessed = await _store(other)._embed_witnessed(["a"])
        assert witnessed is None


@pytest.mark.asyncio
async def test_a_flip_is_not_swallowed_as_a_failed_batch() -> None:
    from app.services.messaging.precision_flip import PrecisionFlip

    store = _store(_Bundled("int8-w"))
    store.logger = MagicMock()
    store._ready_collections = {"c"}
    store._ensure_initialized = AsyncMock()
    entity = MagicMock()
    entity.name = "n"
    entity.org_id = "o"
    store._coalesce_by_key = lambda entities: list(entities)
    store._collection_name_for = lambda org: "c"
    store._key_of = lambda e: "k"
    store._upsert_batch = AsyncMock(side_effect=PrecisionFlip("the index holds fp32"))
    with pytest.raises(PrecisionFlip):
        await store.upsert_entities_batch([entity])
    assert "c" not in store._ready_collections  # checked again on the next write


@pytest.mark.asyncio
async def test_any_other_error_is_still_counted_as_a_failed_batch() -> None:
    store = _store(_Bundled("int8-w"))
    store.logger = MagicMock()
    store._ready_collections = {"c"}
    store._ensure_initialized = AsyncMock()
    entity = MagicMock()
    entity.name = "n"
    entity.org_id = "o"
    entity.entity_type.value = "t"
    entity.entity_id = "e"
    store._coalesce_by_key = lambda entities: list(entities)
    store._collection_name_for = lambda org: "c"
    store._key_of = lambda e: "k"
    store._report = MagicMock()
    store._upsert_batch = AsyncMock(side_effect=RuntimeError("store down"))
    outcome = await store.upsert_entities_batch([entity])
    assert outcome.failed == 1


class TestStoredPointsAreCompared:
    def _entity(self):  # noqa: ANN202
        entity = MagicMock()
        entity.embedding_text = "alpha"
        entity.to_vector_payload.return_value = {"orgId": "o", "name": "alpha"}
        return entity

    def _stored(self, fingerprint: str, **extra):  # noqa: ANN202
        return {
            "exists": True, "page_content": "alpha",
            "metadata": {"orgId": "o", "name": "alpha", "embeddingModel": fingerprint, **extra},
        }

    def test_the_precision_tag_of_a_stored_point_is_not_a_difference(self) -> None:
        entity = self._entity()
        assert EntityVectorStore._same_content(self._stored("fp", embedPrecision="int8-w"), entity, "fp") is True

    def test_a_vector_of_another_fingerprint_is_not_current(self) -> None:
        entity = self._entity()
        assert EntityVectorStore._same_content(self._stored("provider:model:3", embedPrecision="fp32"), entity, "provider:model:3@int8-w") is False


@pytest.mark.asyncio
async def test_the_stamp_point_is_tagged_so_it_never_makes_the_collection_legacy() -> None:
    from app.services.vector_db.collection_pin import STAMP_TAG

    store = _store(_Bundled("fp32"))
    store._shared_collection = "entities"
    store.vector_db_service = MagicMock(upsert_points=AsyncMock())
    stamp = await store._ensure_stamp(in_catalog=False)
    (point,) = store.vector_db_service.upsert_points.await_args.kwargs["points"]
    assert point.payload["metadata"]["embedPrecision"] == STAMP_TAG
    assert point.payload["metadata"]["indexStamp"] == stamp

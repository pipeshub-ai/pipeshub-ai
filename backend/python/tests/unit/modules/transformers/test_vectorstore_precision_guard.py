"""An index keeps one precision: what the vector store does when the server flips under it."""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.precision_flip import PrecisionFlip, precision_flip_in
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from app.utils.precision_witness import PrecisionWitness, set_precision_witness
from tests.unit.modules.transformers.test_vectorstore_point_ids import (
    _make_vectorstore,
    _meta,
)
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

log = logging.getLogger("guard-test")


class _Bundled(EmbeddingServerEmbeddings):
    def __init__(self) -> None:
        self.model = "m"


def _store(db: _FakeDb, sequence: list[tuple[str, bool]]):  # noqa: ANN202
    store = _make_vectorstore()
    store.dense_embeddings = _Bundled()
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "r1"})
    store._guard = PrecisionGuard(CollectionPinStore(ConfigurationService(log, InMemoryKeyValueStore(log)), log), db, log)
    answers = iter(sequence)

    async def embed(texts, record_id, witness_out=None):  # noqa: ANN202
        witness_out.append(next(answers))
        return [[0.1, 0.2] for _ in texts]

    store._embed_documents_with_retry = embed
    store._compute_sparse_embeddings = AsyncMock(side_effect=lambda texts: [None] * len(texts))
    uploaded: list = []

    async def upsert(collection_name, points):  # noqa: ANN202
        uploaded.extend(points)
        for p in points:
            db.points.append({
                "orgId": p.payload["metadata"].get("orgId"), "virtualRecordId": p.payload["metadata"].get("virtualRecordId"),
                "embedPrecision": p.payload["metadata"].get("embedPrecision"),
            })

    store.vector_db_service = db
    db.upsert_points = upsert
    return store, uploaded


async def _batch(store, name="alpha"):  # noqa: ANN202
    await store._embed_and_upsert_documents([Document(page_content=name, metadata=_meta())], "r1", "records")


@pytest.mark.asyncio
async def test_a_flip_between_batches_is_refused_before_the_batch_is_stored() -> None:
    db = _FakeDb()
    store, uploaded = _store(db, [(INT8, False)] * 3 + [(FP32, False)])
    for i in range(3):
        await _batch(store, f"b{i}")
    with pytest.raises(PrecisionFlip):
        await _batch(store, "b3")
    assert len(uploaded) == 3
    assert {p.payload["metadata"]["embedPrecision"] for p in uploaded} == {INT8}  # the invariant over what was upserted


@pytest.mark.asyncio
async def test_the_other_precision_is_refused_from_the_first_batch() -> None:
    db = _FakeDb()
    store, uploaded = _store(db, [(INT8, False), (FP32, False)])
    await _batch(store)
    with pytest.raises(PrecisionFlip):
        await _batch(store)
    assert len(uploaded) == 1


@pytest.mark.asyncio
async def test_a_fallback_batch_on_an_empty_index_stores_nothing_and_claims_nothing() -> None:
    db = _FakeDb()
    store, uploaded = _store(db, [(FP32, True)])
    with pytest.raises(PrecisionFlip):
        await _batch(store)
    assert uploaded == [] and await store._guard._pins.get("records", None, fresh=True) is None


@pytest.mark.asyncio
async def test_the_flip_is_found_through_the_wrappers_between_it_and_the_handler() -> None:
    flip = PrecisionFlip("index holds int8-w")
    wrapped = RuntimeError("Failed to store batch 0")
    wrapped.__cause__ = flip
    outer = RuntimeError("Failed to store documents in vector store")
    outer.__cause__ = wrapped
    assert precision_flip_in(outer) is flip
    assert precision_flip_in(RuntimeError("other")) is None
    assert precision_flip_in(None) is None


class TestNoDeleteWithoutAMatchingWitness:
    @pytest.fixture(autouse=True)
    def _witness(self):  # noqa: ANN202
        set_precision_witness(PrecisionWitness())
        yield
        set_precision_witness(None)

    @pytest.mark.asyncio
    async def test_a_delete_in_front_of_a_write_is_refused_while_the_server_serves_the_other_precision(self) -> None:
        db = _FakeDb()
        db.add(INT8, n=3)
        store, _ = _store(db, [])
        await store._guard._pins.claim("records", INT8, "o1")
        store.dense_embeddings.aembed_documents_witnessed = AsyncMock(return_value=([[0.0]], FP32, False))
        store.dense_embeddings.model = "m"
        db.delete_points = AsyncMock()
        with pytest.raises(PrecisionFlip):
            await store.require_matching_precision_before_delete("records", "o1")
        with pytest.raises(PrecisionFlip):
            await store.delete_blocks_by_ids({"b1"}, "v1", "records", org_id="o1")
        db.delete_points.assert_not_awaited()
        assert len(db.points) == 3  # the old vectors are still there

    @pytest.mark.asyncio
    async def test_a_matching_witness_lets_the_delete_through(self) -> None:
        db = _FakeDb()
        db.add(INT8, n=1)
        store, _ = _store(db, [])
        await store._guard._pins.claim("records", INT8, "o1")
        store.dense_embeddings.aembed_documents_witnessed = AsyncMock(return_value=([[0.0]], INT8, False))
        await store.require_matching_precision_before_delete("records", "o1")

    @pytest.mark.asyncio
    async def test_a_provider_that_does_not_say_is_not_checked(self) -> None:
        store = _make_vectorstore()
        store.dense_embeddings = SimpleNamespace()
        await store.require_matching_precision_before_delete("records", "o1")

    def test_every_delete_before_a_write_is_checked(self) -> None:
        """A new delete that precedes a write must call the check; only deletes that write nothing next may skip it."""
        source = Path(__file__).resolve().parents[4] / "app" / "modules" / "transformers" / "vectorstore.py"
        tree = ast.parse(source.read_text())
        pure_deletions = {"purge_record_vectors", "delete_embeddings", "delete_blocks_by_ids"}
        for function in ast.walk(tree):
            if not isinstance(function, (ast.AsyncFunctionDef, ast.FunctionDef)) or function.name in pure_deletions:
                continue
            calls = {n.func.attr for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            if "delete_embeddings" in calls:
                assert "require_matching_precision_before_delete" in calls, function.name
        deleter = next(f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "delete_blocks_by_ids")
        assert any(
            isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "require_matching_precision_before_delete"
            for n in ast.walk(deleter)
        )


class TestLeaksAtTheEndOfARecord:
    @pytest.mark.asyncio
    async def test_a_leaked_batch_is_found_and_only_it_is_removed(self) -> None:
        db = _FakeDb()
        db.add(INT8, n=3, vrid="v1")
        db.add(FP32, n=2, vrid="v1")
        db.add("image", n=2, vrid="v1")
        store, _ = _store(db, [])
        await store._guard._pins.claim("records", INT8, "o1")
        with pytest.raises(PrecisionFlip, match="removed"):
            await store._check_for_leaks("records", "o1", "v1")
        assert sorted(p["embedPrecision"] for p in db.points) == ["image", "image", INT8, INT8, INT8]

    @pytest.mark.asyncio
    async def test_the_end_of_record_check_reads_the_pin_uncached(self) -> None:
        db = _FakeDb()
        db.add(INT8, n=1, vrid="v1")
        store, _ = _store(db, [])
        await store._guard._pins.claim("records", INT8, "o1")
        used = []
        original = store._guard._pins._config.get_config

        async def watching(key, *a, **k):  # noqa: ANN202
            used.append(k.get("use_cache"))
            return await original(key, *a, **k)

        store._guard._pins._config.get_config = watching
        await store._check_for_leaks("records", "o1", "v1")
        assert used and set(used) == {False}

    @pytest.mark.asyncio
    async def test_a_clean_record_passes_and_a_provider_without_precision_is_skipped(self) -> None:
        db = _FakeDb()
        db.add(INT8, n=3, vrid="v1")
        store, _ = _store(db, [])
        await store._guard._pins.claim("records", INT8, "o1")
        await store._check_for_leaks("records", "o1", "v1")
        store.dense_embeddings = SimpleNamespace()
        await store._check_for_leaks("records", "o1", "v1")

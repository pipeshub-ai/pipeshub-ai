"""P0 regression tests for the SparseEmbedder adapter.

Covers:
- embed_query returns a SparseVector with populated indices/values.
- embed_documents batches in one underlying embed() call, returns results in order.
- Lazy-init is thread-safe: model constructor called exactly once under concurrency.
- Model construction is run via asyncio.to_thread (off the event loop).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vector_db.models import SparseVector
from app.services.vector_db.sparse_embeddings import SparseEmbedder

if TYPE_CHECKING:
    from collections.abc import Callable


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_raw_sparse(indices=(1, 2, 3), values=(0.5, 0.3, 0.2)):
    """Return a duck-type object that looks like a fastembed SparseEmbedding."""
    obj = MagicMock()
    obj.indices = list(indices)
    obj.values = list(values)
    return obj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_query_returns_generic_sparse_vector():
    """embed_query must return a SparseVector with non-empty indices and values."""
    raw = _make_raw_sparse(indices=[10, 20], values=[0.8, 0.2])

    fake_model = MagicMock()
    # fastembed-style: query_embed returns a generator
    fake_model.query_embed.return_value = iter([raw])

    embedder = SparseEmbedder()
    embedder._model = fake_model

    result = await embedder.embed_query("hello world")

    assert isinstance(result, SparseVector)
    assert len(result.indices) > 0
    assert len(result.values) == len(result.indices)
    assert result.indices == [10, 20]
    assert result.values == [0.8, 0.2]


@pytest.mark.asyncio
async def test_embed_documents_batches_in_one_call():
    """embed_documents must call embed() once with all texts and return 50 results."""
    texts = [f"doc {i}" for i in range(50)]
    raw_results = [_make_raw_sparse(indices=[i], values=[float(i) / 50]) for i in range(50)]

    fake_model = MagicMock()
    fake_model.embed.return_value = iter(raw_results)

    embedder = SparseEmbedder()
    embedder._model = fake_model

    results = await embedder.embed_documents(texts)

    # embed() called exactly once
    assert fake_model.embed.call_count == 1
    # All texts passed in one call
    call_args = fake_model.embed.call_args[0][0]
    assert list(call_args) == texts
    # 50 results in order
    assert len(results) == 50
    for i, r in enumerate(results):
        assert isinstance(r, SparseVector)
        assert r.indices == [i]


@pytest.mark.asyncio
async def test_lazy_init_thread_safe():
    """Concurrent embed_query calls must initialise the model exactly once."""
    build_call_count = 0
    real_raw = _make_raw_sparse()

    def _fake_build():
        nonlocal build_call_count
        # Simulate a slow model load
        import time
        time.sleep(0.05)
        build_call_count += 1
        m = MagicMock()
        # Use side_effect so each call gets a fresh iterator (avoids StopIteration in asyncio)
        m.query_embed.side_effect = lambda *args, **kwargs: iter([real_raw])
        m._uses_fastembed = False
        return m

    embedder = SparseEmbedder()

    with patch.object(embedder, "_build_model", side_effect=_fake_build):
        # Launch 10 concurrent callers
        await asyncio.gather(*[embedder.embed_query(f"q{i}") for i in range(10)])

    assert build_call_count == 1, (
        f"Expected model constructor called once, got {build_call_count}"
    )


@pytest.mark.asyncio
async def test_concurrent_callers_share_a_failed_load_and_can_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = SparseEmbedder()
    load_started = asyncio.Event()
    finish_load = asyncio.Event()
    model = object()
    build = MagicMock(side_effect=RuntimeError("Hub unavailable"))

    async def load_in_thread(fn: Callable[[], object]) -> object:
        load_started.set()
        await finish_load.wait()
        return fn()

    monkeypatch.setattr(embedder, "_build_model", build)
    monkeypatch.setattr(asyncio, "to_thread", load_in_thread)

    callers = asyncio.gather(
        *(embedder._ensure_initialized() for _ in range(4)),
        return_exceptions=True,
    )
    await load_started.wait()
    finish_load.set()

    errors = await callers
    assert all(isinstance(error, RuntimeError) for error in errors)
    assert [str(error) for error in errors] == ["Hub unavailable"] * 4
    build.assert_called_once_with()

    build.side_effect = None
    build.return_value = model
    assert await embedder._ensure_initialized() is model
    assert await embedder._ensure_initialized() is model
    assert build.call_count == 2


@pytest.mark.asyncio
async def test_cancelling_first_caller_does_not_restart_the_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = SparseEmbedder()
    load_started = asyncio.Event()
    finish_load = asyncio.Event()
    model = object()

    async def load_in_thread(_fn: Callable[[], object]) -> object:
        load_started.set()
        await finish_load.wait()
        return model

    load = AsyncMock(side_effect=load_in_thread)
    monkeypatch.setattr(asyncio, "to_thread", load)

    first = asyncio.create_task(embedder._ensure_initialized())
    await load_started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(embedder._ensure_initialized())
    finish_load.set()
    assert await second is model
    load.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_does_not_block_event_loop():
    """_build_model must be invoked via asyncio.to_thread, not inline on the loop."""
    called_in_thread = False

    async def _fake_to_thread(fn, *args, **kwargs):
        nonlocal called_in_thread
        called_in_thread = True
        return fn(*args, **kwargs)

    raw = _make_raw_sparse()
    fake_model = MagicMock()
    fake_model.query_embed.return_value = iter([raw])
    fake_model._uses_fastembed = False

    embedder = SparseEmbedder()

    with (
        patch("app.services.vector_db.sparse_embeddings.asyncio.to_thread", side_effect=_fake_to_thread),
        patch.object(embedder, "_build_model", return_value=fake_model),
    ):
        await embedder._ensure_initialized()

    assert called_in_thread, "Model construction must be dispatched via asyncio.to_thread"


def _fake_fastembed(calls: list[bool], *, cached: bool) -> MagicMock:
    class FakeSparse:
        def __init__(self, model_name, **kwargs):
            calls.append(kwargs.get("local_files_only", False))
            if kwargs.get("local_files_only") and not cached:
                raise RuntimeError("not in cache")
            self.model_name = model_name

    fake_module = MagicMock()
    fake_module.SparseTextEmbedding = FakeSparse
    return fake_module


def test_build_model_tries_cache_before_download(monkeypatch) -> None:
    """A cache-only load first (local_files_only=True); the Hub only on a cache miss."""
    import sys

    calls: list[bool] = []
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with patch.dict(sys.modules, {"fastembed": _fake_fastembed(calls, cached=False)}):
        model = SparseEmbedder()._build_model()
    assert calls == [True, False]
    assert model.model_name == "Qdrant/bm25"


def test_build_model_uses_the_cache_without_asking_the_hub(monkeypatch) -> None:
    import sys

    calls: list[bool] = []
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with patch.dict(sys.modules, {"fastembed": _fake_fastembed(calls, cached=True)}):
        SparseEmbedder()._build_model()
    assert calls == [True]


def test_build_model_does_not_download_when_explicitly_offline(monkeypatch) -> None:
    import sys

    calls: list[bool] = []
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with patch.dict(sys.modules, {"fastembed": _fake_fastembed(calls, cached=False)}):
        with pytest.raises(RuntimeError, match="not in cache"):
            SparseEmbedder()._build_model()
    assert calls == [True]


@pytest.mark.parametrize("value", [" true", "true ", "1 ", " yes"])
def test_build_model_downloads_when_hf_hub_offline_is_whitespace_padded(monkeypatch, value) -> None:
    """huggingface_hub does not strip HF_HUB_OFFLINE, so a padded value means online."""
    import sys

    calls: list[bool] = []
    monkeypatch.setenv("HF_HUB_OFFLINE", value)
    with patch.dict(sys.modules, {"fastembed": _fake_fastembed(calls, cached=False)}):
        model = SparseEmbedder()._build_model()
    assert calls == [True, False]
    assert model.model_name == "Qdrant/bm25"


@pytest.mark.parametrize("value", ["on", "ON", "True", "YES"])
def test_build_model_does_not_download_for_every_value_hf_hub_treats_as_offline(monkeypatch, value) -> None:
    import sys

    calls: list[bool] = []
    monkeypatch.setenv("HF_HUB_OFFLINE", value)
    with patch.dict(sys.modules, {"fastembed": _fake_fastembed(calls, cached=False)}):
        with pytest.raises(RuntimeError, match="not in cache"):
            SparseEmbedder()._build_model()
    assert calls == [True]


def test_build_model_leaves_hf_hub_offline_alone(monkeypatch) -> None:
    # huggingface_hub reads HF_HUB_OFFLINE once, at import, so the old runtime toggle did nothing.
    import os
    import sys

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    seen: list[str | None] = []

    class FakeSparse:
        def __init__(self, model_name, **kwargs):
            seen.append(os.environ.get("HF_HUB_OFFLINE"))

    fake_module = MagicMock()
    fake_module.SparseTextEmbedding = FakeSparse
    with patch.dict(sys.modules, {"fastembed": fake_module}):
        SparseEmbedder()._build_model()
    assert seen == [None]
    assert os.environ.get("HF_HUB_OFFLINE") is None


@pytest.mark.asyncio
async def test_embed_documents_empty_returns_empty():
    """embed_documents([]) must short-circuit and return [] without touching the model."""
    embedder = SparseEmbedder()
    embedder._model = MagicMock()

    result = await embedder.embed_documents([])
    assert result == []
    embedder._model.embed.assert_not_called()


@pytest.mark.asyncio
async def test_no_langchain_fallback_fastembed_only():
    """SparseEmbedder uses only fastembed (langchain-qdrant removed as dependency).

    Verify that embed_query and embed_documents use the fastembed API
    (query_embed / embed), not the old langchain-qdrant API.
    """
    raw = _make_raw_sparse(indices=[5, 6], values=[0.6, 0.4])

    fake_model = MagicMock()
    fake_model.query_embed.return_value = iter([raw])
    fake_model.embed.return_value = iter([raw, raw])

    embedder = SparseEmbedder()
    embedder._model = fake_model

    query_result = await embedder.embed_query("test query")
    assert isinstance(query_result, SparseVector)
    fake_model.query_embed.assert_called_once_with("test query")

    doc_results = await embedder.embed_documents(["a", "b"])
    assert len(doc_results) == 2
    fake_model.embed.assert_called_once_with(["a", "b"])

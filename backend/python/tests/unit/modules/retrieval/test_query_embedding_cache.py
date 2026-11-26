"""The query-embedding cache: the same query is searched with the same vector for a while."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.retrieval.query_embedding_cache import QueryEmbeddingCache


class TestQueryEmbeddingCache:
    def test_returns_what_was_stored_for_the_same_org_config_and_text(self) -> None:
        cache = QueryEmbeddingCache(ttl_seconds=60, max_entries=10)
        cache.put("org", "cfg", "q", [0.1, 0.2])
        assert cache.get("org", "cfg", "q") == [0.1, 0.2]

    @pytest.mark.parametrize("key", [("other-org", "cfg", "q"), ("org", "other-cfg", "q"), ("org", "cfg", "q ")])
    def test_a_different_org_config_or_text_misses(self, key: tuple) -> None:
        cache = QueryEmbeddingCache(ttl_seconds=60, max_entries=10)
        cache.put("org", "cfg", "q", [0.1])
        assert cache.get(*key) is None

    def test_an_unknown_config_is_never_cached(self) -> None:
        cache = QueryEmbeddingCache(ttl_seconds=60, max_entries=10)
        cache.put("org", None, "q", [0.1])
        assert cache.get("org", None, "q") is None

    def test_an_entry_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = [1000.0]
        monkeypatch.setattr("app.modules.retrieval.query_embedding_cache.time.monotonic", lambda: now[0])
        cache = QueryEmbeddingCache(ttl_seconds=60, max_entries=10)
        cache.put("org", "cfg", "q", [0.1])
        now[0] += 61
        assert cache.get("org", "cfg", "q") is None

    def test_the_oldest_unused_entry_goes_first_when_full(self) -> None:
        cache = QueryEmbeddingCache(ttl_seconds=60, max_entries=2)
        cache.put("o", "c", "a", [1.0])
        cache.put("o", "c", "b", [2.0])
        cache.get("o", "c", "a")
        cache.put("o", "c", "c", [3.0])
        assert cache.get("o", "c", "b") is None
        assert cache.get("o", "c", "a") == [1.0]

    def test_a_caller_cannot_change_a_stored_vector(self) -> None:
        cache = QueryEmbeddingCache(ttl_seconds=60, max_entries=10)
        vector = [0.1]
        cache.put("o", "c", "q", vector)
        vector[0] = 9.0
        got = cache.get("o", "c", "q")
        got[0] = 7.0  # type: ignore[index]
        assert cache.get("o", "c", "q") == [0.1]

    @pytest.mark.parametrize("ttl,size", [(0, 10), (60, 0)])
    def test_turned_off_by_zero(self, ttl: float, size: int) -> None:
        cache = QueryEmbeddingCache(ttl_seconds=ttl, max_entries=size)
        cache.put("o", "c", "q", [0.1])
        assert cache.get("o", "c", "q") is None

    def test_settings_come_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_EMBEDDING_CACHE_SECONDS", "0")
        assert QueryEmbeddingCache().enabled is False
        monkeypatch.setenv("QUERY_EMBEDDING_CACHE_SECONDS", "junk")
        assert QueryEmbeddingCache().ttl == 600.0


class TestRetrievalServiceUsesIt:
    @pytest.mark.asyncio
    async def test_a_repeated_query_is_embedded_once_and_searched_with_the_same_vector(self) -> None:
        from app.modules.retrieval.retrieval_service import RetrievalService

        service = RetrievalService.__new__(RetrievalService)
        model = MagicMock()
        calls = iter([[0.10, 0.20], [0.11, 0.19]])  # what a remote model may send for the same text
        model.aembed_query = AsyncMock(side_effect=lambda _text: next(calls))
        service.get_embedding_model_instance = AsyncMock(return_value=model)
        service._cached_embedding_is_local = False
        service._cached_embedding_config_hash = "cfg-1"
        service._query_embeddings = QueryEmbeddingCache(ttl_seconds=60, max_entries=10)
        service._ensure_sparse_embedder = AsyncMock(return_value=None)
        service._capabilities = MagicMock(supports_sparse_vectors=False)
        service._run_searches = AsyncMock(return_value=[])

        for _ in range(2):
            await service._execute_parallel_searches(["onboarding"], None, 5, "org-1", "user-1")
        sent = [c.args[5] for c in service._run_searches.await_args_list]
        assert sent[0] == sent[1] == [[0.10, 0.20]]
        assert model.aembed_query.await_count == 1

    @pytest.mark.asyncio
    async def test_a_new_embedding_config_embeds_again(self) -> None:
        from app.modules.retrieval.retrieval_service import RetrievalService

        service = RetrievalService.__new__(RetrievalService)
        model = MagicMock()
        model.aembed_query = AsyncMock(side_effect=[[0.1], [0.9]])
        service.get_embedding_model_instance = AsyncMock(return_value=model)
        service._cached_embedding_is_local = False
        service._query_embeddings = QueryEmbeddingCache(ttl_seconds=60, max_entries=10)
        service._ensure_sparse_embedder = AsyncMock(return_value=None)
        service._capabilities = MagicMock(supports_sparse_vectors=False)
        service._run_searches = AsyncMock(return_value=[])

        service._cached_embedding_config_hash = "cfg-1"
        await service._execute_parallel_searches(["q"], None, 5, "org-1", "user-1")
        service._cached_embedding_config_hash = "cfg-2"
        await service._execute_parallel_searches(["q"], None, 5, "org-1", "user-1")
        assert [c.args[5] for c in service._run_searches.await_args_list] == [[[0.1]], [[0.9]]]

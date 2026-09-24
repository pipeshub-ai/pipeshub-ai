"""The skill-vector cache is what keeps `factory.create` off the embedding
service on every chat request — a fresh `SkillManager` is built per request and
calls `rebuild()` each time."""

import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.modules.providers.skills.base import SkillMetadata, SkillStatus
from app.agents.agent_loop.skills import semantic_index as si


def _skill(name: str, description: str = "does a thing") -> SkillMetadata:
    return SkillMetadata(
        name=name, description=description, tags=[], concepts=[],
        status=SkillStatus.ACTIVE,
    )


def _index_with(embedder) -> si.SemanticSkillIndex:
    retrieval = MagicMock()
    retrieval.get_embedding_model_instance = AsyncMock(return_value=embedder)
    return si.SemanticSkillIndex(retrieval)


def _embedder(dim: int = 3, model: str = "test-model") -> MagicMock:
    e = MagicMock()
    e.model = model
    e.aembed_documents = AsyncMock(side_effect=lambda texts: [[float(len(t))] * dim for t in texts])
    return e


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    si._vector_cache.clear()
    yield
    si._vector_cache.clear()


class TestVectorCache:
    @pytest.mark.asyncio
    async def test_second_rebuild_embeds_nothing(self) -> None:
        """The per-request rebuild that used to re-embed the whole catalog."""
        skills = [_skill("a"), _skill("b")]
        embedder = _embedder()

        await _index_with(embedder).rebuild(skills)
        assert embedder.aembed_documents.await_count == 1

        # A second request, a brand-new index instance — as happens per chat.
        index2 = _index_with(embedder)
        await index2.rebuild(skills)

        assert embedder.aembed_documents.await_count == 1  # no new call
        assert set(index2._vectors) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_only_changed_skills_are_re_embedded(self) -> None:
        embedder = _embedder()
        await _index_with(embedder).rebuild([_skill("a"), _skill("b")])
        embedder.aembed_documents.reset_mock()

        await _index_with(embedder).rebuild([_skill("a"), _skill("b", "now does something else")])

        assert embedder.aembed_documents.await_count == 1
        assert embedder.aembed_documents.await_args.args[0] == ["b: now does something else  "]

    @pytest.mark.asyncio
    async def test_switching_embedding_model_re_embeds(self) -> None:
        """Vectors from one model must never be served to another."""
        skills = [_skill("a")]
        await _index_with(_embedder(model="model-one")).rebuild(skills)

        other = _embedder(dim=5, model="model-two")
        index = _index_with(other)
        await index.rebuild(skills)

        assert other.aembed_documents.await_count == 1
        assert len(index._vectors["a"]) == 5

    @pytest.mark.asyncio
    async def test_embedding_failure_keeps_cached_vectors(self) -> None:
        embedder = _embedder()
        await _index_with(embedder).rebuild([_skill("a")])

        failing = _embedder()
        failing.aembed_documents = AsyncMock(side_effect=RuntimeError("embedder down"))
        index = _index_with(failing)
        await index.rebuild([_skill("a"), _skill("new")])

        # "a" was cached and still scores semantically; "new" falls back to keyword.
        assert set(index._vectors) == {"a"}


class TestVectorCacheSizing:
    """`PIPESHUB_SKILL_VECTOR_CACHE_SIZE` is a process-level memory knob, so a
    bad value must degrade to the default rather than break the module.

    Each of these used to fail: a non-numeric value raised `ValueError` at
    *import*, taking down every importer of this module; 0 or a negative value
    built an `LRUCache` that raises `ValueError: value too large` on the first
    write — inside the embedding path, whose only fallback is silent keyword
    scoring.
    """

    @pytest.fixture(autouse=True)
    def _restore_module(self, monkeypatch) -> Iterator[None]:
        """`importlib.reload` rebinds this module's globals for the rest of the
        session, so the env is reset and the module reloaded once more on the
        way out — otherwise a later test in another file inherits whichever
        cache size ran last here.

        Restores the *original* ambient value (not just "unset") before that
        final reload: an environment that legitimately sets
        PIPESHUB_SKILL_VECTOR_CACHE_SIZE must not leave the module reloaded
        against the default while the variable itself reverts around it.
        """
        original = os.environ.get("PIPESHUB_SKILL_VECTOR_CACHE_SIZE")
        yield
        if original is None:
            monkeypatch.delenv("PIPESHUB_SKILL_VECTOR_CACHE_SIZE", raising=False)
        else:
            monkeypatch.setenv("PIPESHUB_SKILL_VECTOR_CACHE_SIZE", original)
        import importlib

        importlib.reload(si)

    @staticmethod
    def _reload(monkeypatch, value: str | None) -> object:
        import importlib

        if value is None:
            monkeypatch.delenv("PIPESHUB_SKILL_VECTOR_CACHE_SIZE", raising=False)
        else:
            monkeypatch.setenv("PIPESHUB_SKILL_VECTOR_CACHE_SIZE", value)
        return importlib.reload(si)

    @pytest.mark.parametrize("value", ["abc", "", "  ", "1.5"])
    def test_a_malformed_value_falls_back_to_the_default(
        self, monkeypatch, value: str
    ) -> None:
        module = self._reload(monkeypatch, value)
        assert module._VECTOR_CACHE_MAX == 4096

    @pytest.mark.parametrize("value", ["0", "-5"])
    def test_a_non_positive_value_still_yields_a_usable_cache(
        self, monkeypatch, value: str
    ) -> None:
        module = self._reload(monkeypatch, value)
        assert module._VECTOR_CACHE_MAX >= 1
        # The regression: LRUCache(maxsize=0) raises on the first insert.
        module._vector_cache["k"] = [0.1, 0.2]
        assert module._vector_cache["k"] == [0.1, 0.2]

    def test_a_valid_value_is_honoured(self, monkeypatch) -> None:
        module = self._reload(monkeypatch, "256")
        assert module._VECTOR_CACHE_MAX == 256
        assert module._vector_cache.maxsize == 256

    def test_unset_uses_the_default(self, monkeypatch) -> None:
        module = self._reload(monkeypatch, None)
        assert module._VECTOR_CACHE_MAX == 4096

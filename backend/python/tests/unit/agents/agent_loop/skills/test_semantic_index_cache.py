"""The skill-vector cache is what keeps `factory.create` off the embedding
service on every chat request — a fresh `SkillManager` is built per request and
calls `rebuild()` each time."""

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

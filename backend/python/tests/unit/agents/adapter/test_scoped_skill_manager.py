"""Tests for app.agents.agent_loop.skills.scoped_manager.ScopedSkillManager —
verifies the per-agent assignment allowlist narrows catalog/search/activation
without touching the underlying SkillManager's other surface (delegated
transparently via __getattr__)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agents.agent_loop.skills.scoped_manager import ScopedSkillManager


def _metadata(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _match(name: str) -> SimpleNamespace:
    return SimpleNamespace(skill=SimpleNamespace(name=name))


class TestCatalogSnapshot:
    def test_filters_to_allowed_names(self) -> None:
        manager = MagicMock()
        manager.catalog_snapshot = MagicMock(
            return_value=[_metadata("pdf-extractor"), _metadata("csv-summarizer"), _metadata("web-search")]
        )
        scoped = ScopedSkillManager(manager, {"pdf-extractor", "web-search"})
        result = scoped.catalog_snapshot()
        assert {m.name for m in result} == {"pdf-extractor", "web-search"}

    def test_empty_allowlist_hides_everything(self) -> None:
        manager = MagicMock()
        manager.catalog_snapshot = MagicMock(return_value=[_metadata("pdf-extractor")])
        scoped = ScopedSkillManager(manager, set())
        assert scoped.catalog_snapshot() == []


class TestListSkills:
    @pytest.mark.asyncio
    async def test_filters_async_results(self) -> None:
        manager = MagicMock()
        manager.list_skills = AsyncMock(
            return_value=[_metadata("pdf-extractor"), _metadata("csv-summarizer")]
        )
        scoped = ScopedSkillManager(manager, {"csv-summarizer"})
        result = await scoped.list_skills()
        assert [m.name for m in result] == ["csv-summarizer"]
        manager.list_skills.assert_awaited_once_with(None)


class TestSearch:
    @pytest.mark.asyncio
    async def test_overfetches_and_filters_to_limit(self) -> None:
        manager = MagicMock()
        # Simulate the underlying index returning a mix of visible/invisible
        # matches beyond the requested limit — scoped search must still
        # return up to `limit` VISIBLE matches, not truncate before filtering.
        manager.search = AsyncMock(
            return_value=[
                _match("not-allowed-1"),
                _match("pdf-extractor"),
                _match("not-allowed-2"),
                _match("csv-summarizer"),
                _match("web-search"),
            ]
        )
        scoped = ScopedSkillManager(manager, {"pdf-extractor", "csv-summarizer", "web-search"})
        result = await scoped.search("extract", limit=2)
        assert [m.skill.name for m in result] == ["pdf-extractor", "csv-summarizer"]
        # limit passed upstream must be over-fetched, never the raw requested limit.
        _, kwargs = manager.search.await_args
        assert kwargs["limit"] > 2

    @pytest.mark.asyncio
    async def test_forwards_filter_kwargs(self) -> None:
        manager = MagicMock()
        manager.search = AsyncMock(return_value=[])
        scoped = ScopedSkillManager(manager, {"pdf-extractor"})
        await scoped.search("q", category="documents", subcategory="pdf", tags=["a"], limit=5)
        _, kwargs = manager.search.await_args
        assert kwargs["category"] == "documents"
        assert kwargs["subcategory"] == "pdf"
        assert kwargs["tags"] == ["a"]


class TestActivateSkill:
    @pytest.mark.asyncio
    async def test_allowed_skill_delegates(self) -> None:
        manager = MagicMock()
        manager.activate_skill = AsyncMock(return_value=SimpleNamespace(metadata=_metadata("pdf-extractor")))
        scoped = ScopedSkillManager(manager, {"pdf-extractor"})
        result = await scoped.activate_skill("pdf-extractor", session_id="s1")
        manager.activate_skill.assert_awaited_once_with("pdf-extractor", "s1")
        assert result.metadata.name == "pdf-extractor"

    @pytest.mark.asyncio
    async def test_disallowed_skill_raises_without_calling_manager(self) -> None:
        manager = MagicMock()
        manager.activate_skill = AsyncMock()
        scoped = ScopedSkillManager(manager, {"pdf-extractor"})
        with pytest.raises(RegistryError, match="not assigned"):
            await scoped.activate_skill("csv-summarizer")
        manager.activate_skill.assert_not_called()


class TestLoadResource:
    @pytest.mark.asyncio
    async def test_allowed_skill_delegates(self) -> None:
        manager = MagicMock()
        manager.load_resource = AsyncMock(return_value="print('hi')")
        scoped = ScopedSkillManager(manager, {"pdf-extractor"})
        result = await scoped.load_resource("pdf-extractor", "scripts/run.py")
        assert result == "print('hi')"
        manager.load_resource.assert_awaited_once_with("pdf-extractor", "scripts/run.py")

    @pytest.mark.asyncio
    async def test_disallowed_skill_raises_without_calling_manager(self) -> None:
        manager = MagicMock()
        manager.load_resource = AsyncMock()
        scoped = ScopedSkillManager(manager, {"pdf-extractor"})
        with pytest.raises(RegistryError, match="not assigned"):
            await scoped.load_resource("csv-summarizer", "scripts/run.py")
        manager.load_resource.assert_not_called()


class TestTransparentDelegation:
    @pytest.mark.asyncio
    async def test_unscoped_methods_pass_through_via_getattr(self) -> None:
        """create/update/delete/versions/etc. aren't overridden — the wrapper
        must forward them untouched to the underlying manager."""
        manager = MagicMock()
        manager.create = AsyncMock(return_value="created")
        scoped = ScopedSkillManager(manager, {"pdf-extractor"})
        result = await scoped.create(name="new-skill")
        manager.create.assert_awaited_once_with(name="new-skill")
        assert result == "created"

    def test_attribute_access_delegates(self) -> None:
        manager = MagicMock()
        manager.some_property = "value"
        scoped = ScopedSkillManager(manager, set())
        assert scoped.some_property == "value"

"""Tests for ``app.agents.agent_loop.skills.manager_factory``."""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.modules.providers.skills.manager import SkillManagerConfig


class TestEnvBool:
    def test_true_string(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _env_bool
        with patch.dict(os.environ, {"TEST_FLAG": "true"}):
            assert _env_bool("TEST_FLAG", False) is True

    def test_false_string(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _env_bool
        with patch.dict(os.environ, {"TEST_FLAG": "false"}):
            assert _env_bool("TEST_FLAG", True) is False

    def test_missing_uses_default_true(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _env_bool
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_MISSING_FLAG", None)
            assert _env_bool("TEST_MISSING_FLAG", True) is True

    def test_missing_uses_default_false(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _env_bool
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_MISSING_FLAG", None)
            assert _env_bool("TEST_MISSING_FLAG", False) is False


class TestSkillsManagerConfig:
    def test_default_config(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import skills_manager_config
        with patch.dict(os.environ, {}, clear=False):
            for key in [
                "PIPESHUB_SKILLS_AUTO_APPROVE",
                "PIPESHUB_SKILLS_WRITE_APPROVAL",
                "PIPESHUB_SKILLS_LEARNING_ENABLED",
                "PIPESHUB_SKILLS_CATALOG_RENDER_LIMIT",
            ]:
                os.environ.pop(key, None)
            config = skills_manager_config()
        assert config.auto_approve is False
        assert config.write_approval is True
        assert config.learning_enabled is False
        assert config.catalog_render_limit == 40

    def test_env_overrides(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import skills_manager_config
        with patch.dict(os.environ, {
            "PIPESHUB_SKILLS_AUTO_APPROVE": "true",
            "PIPESHUB_SKILLS_WRITE_APPROVAL": "false",
            "PIPESHUB_SKILLS_LEARNING_ENABLED": "true",
            "PIPESHUB_SKILLS_CATALOG_RENDER_LIMIT": "100",
        }):
            config = skills_manager_config()
        assert config.auto_approve is True
        assert config.write_approval is False
        assert config.learning_enabled is True
        assert config.catalog_render_limit == 100


class TestBuildGovernor:
    def test_manual_review_governor_by_default(self) -> None:
        from app.agent_loop_lib.modules.providers.skills.governor import ManualReviewGovernor
        from app.agents.agent_loop.skills.manager_factory import build_governor
        config = SkillManagerConfig(auto_approve=False, write_approval=True)
        gov = build_governor(config, graph_provider=None, org_id="o", user_id="u")
        assert isinstance(gov, ManualReviewGovernor)

    def test_auto_approve_governor(self) -> None:
        from app.agent_loop_lib.modules.providers.skills.governor import AutoApproveGovernor
        from app.agents.agent_loop.skills.manager_factory import build_governor
        config = SkillManagerConfig(auto_approve=True, write_approval=False)
        gov = build_governor(config, graph_provider=None, org_id="o", user_id="u")
        assert isinstance(gov, AutoApproveGovernor)

    def test_audit_governor_wraps_when_graph_provider_present(self) -> None:
        from app.agents.agent_loop.skills.audit_governor import AuditGovernor
        from app.agents.agent_loop.skills.manager_factory import build_governor
        config = SkillManagerConfig(auto_approve=False, write_approval=True)
        gov = build_governor(config, graph_provider=MagicMock(), org_id="o", user_id="u")
        assert isinstance(gov, AuditGovernor)

    def test_no_graph_provider_skips_audit(self) -> None:
        from app.agent_loop_lib.modules.providers.skills.governor import ManualReviewGovernor
        from app.agents.agent_loop.skills.manager_factory import build_governor
        config = SkillManagerConfig(auto_approve=False, write_approval=True)
        gov = build_governor(config, graph_provider=None, org_id="o", user_id="u")
        assert isinstance(gov, ManualReviewGovernor)


class TestGetBuiltinSeeder:
    def test_returns_none_when_packs_dir_missing(self) -> None:
        import app.agents.agent_loop.skills.manager_factory as mf
        old_seeder = mf._builtin_seeder
        old_failed = mf._builtin_seeder_load_failed
        try:
            mf._builtin_seeder = None
            mf._builtin_seeder_load_failed = False
            with patch.object(mf, "_BUILTIN_PACKS_ROOT", "/nonexistent/path"):
                result = mf.get_builtin_seeder()
            assert result is None
            assert mf._builtin_seeder_load_failed is True
        finally:
            mf._builtin_seeder = old_seeder
            mf._builtin_seeder_load_failed = old_failed

    def test_caches_failure(self) -> None:
        import app.agents.agent_loop.skills.manager_factory as mf
        old_seeder = mf._builtin_seeder
        old_failed = mf._builtin_seeder_load_failed
        try:
            mf._builtin_seeder = None
            mf._builtin_seeder_load_failed = True
            result = mf.get_builtin_seeder()
            assert result is None
        finally:
            mf._builtin_seeder = old_seeder
            mf._builtin_seeder_load_failed = old_failed

    def test_returns_cached_seeder(self) -> None:
        import app.agents.agent_loop.skills.manager_factory as mf
        old_seeder = mf._builtin_seeder
        old_failed = mf._builtin_seeder_load_failed
        sentinel = MagicMock()
        try:
            mf._builtin_seeder = sentinel
            mf._builtin_seeder_load_failed = False
            result = mf.get_builtin_seeder()
            assert result is sentinel
        finally:
            mf._builtin_seeder = old_seeder
            mf._builtin_seeder_load_failed = old_failed


class TestSyncBuiltinSkills:
    @pytest.mark.asyncio
    async def test_noop_when_seeder_none(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import sync_builtin_skills
        manager = MagicMock()
        with patch(
            "app.agents.agent_loop.skills.manager_factory.get_builtin_seeder",
            return_value=None,
        ):
            await sync_builtin_skills(MagicMock(), "org-1", manager)
        manager.catalog_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_versions_match(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import sync_builtin_skills
        seeder = MagicMock()
        seeder.pack_versions = {"pack1": "v1"}
        skill_meta = MagicMock()
        skill_meta.name = "pack1"
        skill_meta.pack_version = "v1"
        skill_meta.source = "builtin"

        manager = MagicMock()
        manager.catalog_snapshot.return_value = [skill_meta]

        with patch(
            "app.agents.agent_loop.skills.manager_factory.get_builtin_seeder",
            return_value=seeder,
        ), patch(
            "app.agents.agent_loop.skills.manager_factory.SkillSource",
        ) as mock_source:
            mock_source.BUILTIN = "builtin"
            await sync_builtin_skills(MagicMock(), "org-1", manager)
        seeder.sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_called_on_version_mismatch(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import sync_builtin_skills
        seeder = MagicMock()
        seeder.pack_versions = {"pack1": "v2"}
        seeder.sync = AsyncMock()

        skill_meta = MagicMock()
        skill_meta.name = "pack1"
        skill_meta.pack_version = "v1"
        skill_meta.source = "builtin"

        manager = MagicMock()
        manager.catalog_snapshot.return_value = [skill_meta]
        manager.refresh = AsyncMock()

        with patch(
            "app.agents.agent_loop.skills.manager_factory.get_builtin_seeder",
            return_value=seeder,
        ), patch(
            "app.agents.agent_loop.skills.manager_factory.SkillSource",
        ) as mock_source, patch(
            "app.agents.agent_loop.skills.manager_factory.GraphSkillStore",
        ):
            mock_source.BUILTIN = "builtin"
            await sync_builtin_skills(MagicMock(), "org-1", manager)
        seeder.sync.assert_awaited_once()
        manager.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_exception_swallowed(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import sync_builtin_skills
        seeder = MagicMock()
        seeder.pack_versions = {"pack1": "v2"}
        seeder.sync = AsyncMock(side_effect=RuntimeError("sync error"))

        manager = MagicMock()
        manager.catalog_snapshot.return_value = []

        with patch(
            "app.agents.agent_loop.skills.manager_factory.get_builtin_seeder",
            return_value=seeder,
        ), patch(
            "app.agents.agent_loop.skills.manager_factory.GraphSkillStore",
        ):
            await sync_builtin_skills(MagicMock(), "org-1", manager)
        manager.refresh.assert_not_called()


class TestBuildRuntimeSkillManager:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_graph_provider(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import build_runtime_skill_manager
        from types import SimpleNamespace
        context = SimpleNamespace(graph_provider=None, org_id="o", user_id="u", retrieval_service=None)
        registry = MagicMock()
        result = await build_runtime_skill_manager(context, registry)
        assert result is None

    @pytest.mark.asyncio
    async def test_builds_manager_with_graph_provider(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import build_runtime_skill_manager
        from types import SimpleNamespace
        fake_manager = MagicMock()
        fake_manager.start = AsyncMock()

        context = SimpleNamespace(
            graph_provider=MagicMock(), org_id="o", user_id="u", retrieval_service=MagicMock(),
        )
        registry = MagicMock()

        with patch(
            "app.agents.agent_loop.skills.manager_factory._build_manager",
            new_callable=AsyncMock,
            return_value=fake_manager,
        ), patch(
            "app.agents.agent_loop.skills.manager_factory.sync_builtin_skills",
            new_callable=AsyncMock,
        ):
            result = await build_runtime_skill_manager(context, registry)
        assert result is fake_manager


class TestBuildManagerInternal:
    """Direct tests for ``_build_manager`` — covers lines 159-175."""

    @pytest.mark.asyncio
    async def test_no_extractor_when_transport_is_none(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _build_manager

        mock_manager = MagicMock()
        mock_manager.start = AsyncMock()

        with patch("app.agents.agent_loop.skills.manager_factory.GraphSkillStore") as mock_store, \
             patch("app.agents.agent_loop.skills.manager_factory.SemanticSkillIndex"), \
             patch("app.agents.agent_loop.skills.manager_factory.GraphUsageTracker"), \
             patch("app.agents.agent_loop.skills.manager_factory.RubricSkillEvaluator"), \
             patch("app.agents.agent_loop.skills.manager_factory.SkillValidator"), \
             patch("app.agents.agent_loop.skills.manager_factory.LLMSkillExtractor") as mock_extractor_cls, \
             patch("app.agents.agent_loop.skills.manager_factory.SkillManager", return_value=mock_manager), \
             patch("app.agents.agent_loop.skills.manager_factory.build_governor"):
            config = SkillManagerConfig(learning_enabled=True)
            result = await _build_manager(
                MagicMock(), "org", "user", config,
                retrieval_service=MagicMock(),
                extractor_transport=None,
            )
        mock_extractor_cls.assert_not_called()
        assert result is mock_manager
        mock_manager.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extractor_created_when_transport_and_learning_enabled(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _build_manager

        mock_manager = MagicMock()
        mock_manager.start = AsyncMock()
        mock_transport = MagicMock()

        with patch("app.agents.agent_loop.skills.manager_factory.GraphSkillStore"), \
             patch("app.agents.agent_loop.skills.manager_factory.SemanticSkillIndex"), \
             patch("app.agents.agent_loop.skills.manager_factory.GraphUsageTracker"), \
             patch("app.agents.agent_loop.skills.manager_factory.RubricSkillEvaluator"), \
             patch("app.agents.agent_loop.skills.manager_factory.SkillValidator"), \
             patch("app.agents.agent_loop.skills.manager_factory.LLMSkillExtractor") as mock_extractor_cls, \
             patch("app.agents.agent_loop.skills.manager_factory.SkillManager", return_value=mock_manager) as mock_sm, \
             patch("app.agents.agent_loop.skills.manager_factory.build_governor"), \
             patch("app.agent_loop_lib.transport.registry.LazyTransport") as mock_lazy:
            config = SkillManagerConfig(learning_enabled=True)
            result = await _build_manager(
                MagicMock(), "org", "user", config,
                retrieval_service=MagicMock(),
                extractor_transport=mock_transport,
            )
        mock_lazy.assert_called_once_with(mock_transport, "langchain")
        mock_extractor_cls.assert_called_once()
        call_kwargs = mock_sm.call_args[1]
        assert call_kwargs["extractor"] is mock_extractor_cls.return_value

    @pytest.mark.asyncio
    async def test_no_extractor_when_learning_disabled(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _build_manager

        mock_manager = MagicMock()
        mock_manager.start = AsyncMock()

        with patch("app.agents.agent_loop.skills.manager_factory.GraphSkillStore"), \
             patch("app.agents.agent_loop.skills.manager_factory.SemanticSkillIndex"), \
             patch("app.agents.agent_loop.skills.manager_factory.GraphUsageTracker"), \
             patch("app.agents.agent_loop.skills.manager_factory.RubricSkillEvaluator"), \
             patch("app.agents.agent_loop.skills.manager_factory.SkillValidator"), \
             patch("app.agents.agent_loop.skills.manager_factory.LLMSkillExtractor") as mock_extractor_cls, \
             patch("app.agents.agent_loop.skills.manager_factory.SkillManager", return_value=mock_manager) as mock_sm, \
             patch("app.agents.agent_loop.skills.manager_factory.build_governor"):
            config = SkillManagerConfig(learning_enabled=False)
            result = await _build_manager(
                MagicMock(), "org", "user", config,
                retrieval_service=MagicMock(),
                extractor_transport=MagicMock(),
            )
        mock_extractor_cls.assert_not_called()
        call_kwargs = mock_sm.call_args[1]
        assert call_kwargs["extractor"] is None

    @pytest.mark.asyncio
    async def test_visibility_scope_passed_to_store(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _build_manager

        mock_manager = MagicMock()
        mock_manager.start = AsyncMock()

        with patch("app.agents.agent_loop.skills.manager_factory.GraphSkillStore") as mock_store, \
             patch("app.agents.agent_loop.skills.manager_factory.SemanticSkillIndex"), \
             patch("app.agents.agent_loop.skills.manager_factory.GraphUsageTracker"), \
             patch("app.agents.agent_loop.skills.manager_factory.RubricSkillEvaluator"), \
             patch("app.agents.agent_loop.skills.manager_factory.SkillValidator"), \
             patch("app.agents.agent_loop.skills.manager_factory.LLMSkillExtractor"), \
             patch("app.agents.agent_loop.skills.manager_factory.SkillManager", return_value=mock_manager), \
             patch("app.agents.agent_loop.skills.manager_factory.build_governor"):
            gp = MagicMock()
            config = SkillManagerConfig(learning_enabled=False)
            await _build_manager(
                gp, "org", "user", config,
                retrieval_service=MagicMock(),
                extractor_transport=None,
                visibility_scope="creator-scope",
            )
        mock_store.assert_called_once_with(gp, "org", "user", visibility_scope="creator-scope")

    @pytest.mark.asyncio
    async def test_manager_start_awaited(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import _build_manager

        mock_manager = MagicMock()
        mock_manager.start = AsyncMock()

        with patch("app.agents.agent_loop.skills.manager_factory.GraphSkillStore"), \
             patch("app.agents.agent_loop.skills.manager_factory.SemanticSkillIndex"), \
             patch("app.agents.agent_loop.skills.manager_factory.GraphUsageTracker"), \
             patch("app.agents.agent_loop.skills.manager_factory.RubricSkillEvaluator"), \
             patch("app.agents.agent_loop.skills.manager_factory.SkillValidator"), \
             patch("app.agents.agent_loop.skills.manager_factory.LLMSkillExtractor"), \
             patch("app.agents.agent_loop.skills.manager_factory.SkillManager", return_value=mock_manager), \
             patch("app.agents.agent_loop.skills.manager_factory.build_governor"):
            config = SkillManagerConfig(learning_enabled=False)
            await _build_manager(
                MagicMock(), "org", "user", config,
                retrieval_service=MagicMock(),
                extractor_transport=None,
            )
        mock_manager.start.assert_awaited_once()


class TestBuildManagementSkillManager:
    @pytest.mark.asyncio
    async def test_builds_management_manager(self) -> None:
        from app.agents.agent_loop.skills.manager_factory import build_management_skill_manager
        fake_manager = MagicMock()

        with patch(
            "app.agents.agent_loop.skills.manager_factory._build_manager",
            new_callable=AsyncMock,
            return_value=fake_manager,
        ):
            result = await build_management_skill_manager(
                graph_provider=MagicMock(),
                org_id="o",
                user_id="u",
                retrieval_service=MagicMock(),
            )
        assert result is fake_manager

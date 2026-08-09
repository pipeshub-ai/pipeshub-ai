"""`tasks_wiring.py` (Phase 7) -- `tasks_enabled()`'s default-on kill-switch
(same convention as `test_skills_wiring_gate.py`) and `build_task_engine`'s
"never break request construction over an optional feature" contract:
`None` whenever `graph_provider`/`config_service` aren't wired, AND when
building the engine's Redis/producer dependencies fails for any other
reason -- regression coverage for a real bug where an un-awaitable
`config_service.get_redis_config()` (e.g. a bare `MagicMock` in tests, or
Redis unreachable in production) crashed EVERY agent turn, not just the
ones that needed `task_find`/`task_manage`."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.agent_loop.tasks_wiring import build_task_engine, tasks_enabled
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    import pytest


class TestTasksEnabledDefault:
    def test_defaults_to_enabled_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_ENABLE_TASKS", raising=False)
        assert tasks_enabled() is True

    def test_explicit_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_TASKS", "false")
        assert tasks_enabled() is False

    def test_explicit_true_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_TASKS", "true")
        assert tasks_enabled() is True


class TestBuildTaskEngineDegradesGracefully:
    async def test_none_without_graph_provider(self) -> None:
        context = make_context(graph_provider=None)
        assert await build_task_engine(context) is None

    async def test_none_without_config_service(self) -> None:
        context = make_context(config_service=None)
        assert await build_task_engine(context) is None

    async def test_none_when_dependency_setup_raises_instead_of_crashing_the_turn(self) -> None:
        """`make_context()`'s default `config_service` is a bare `MagicMock`
        -- `get_redis_config()` on it is not awaitable, reproducing the
        exact "task engine setup breaks unrelated chat turns" regression."""
        context = make_context()
        assert await build_task_engine(context) is None

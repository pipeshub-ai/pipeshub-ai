"""Verify that ``register_task_tools`` exposes only the *workflow* tool
vocabulary to the agent and never registers the ``task_find``/``task_manage``
aliases.

Background: both ``task_manage`` and ``workflow_manage`` call the same
``TaskEngine`` methods, but only ``workflow_manage`` has codegen wired in.
Exposing both confused the model — it would pick ``task_manage``, silently
producing agent-task-only workflows. Keeping one clean tool group eliminates
the ambiguity.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.tasks_wiring import (
    TASK_TOOL_NAMES,
    register_task_tools,
)
from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.domain.models import TaskDefinition, TaskPrincipal, TaskStatus
from tests.unit.agents.adapter.conftest import make_context


def _make_engine() -> MagicMock:
    engine = MagicMock(spec=TaskEngine)
    engine.create = AsyncMock()
    engine.update_fields = AsyncMock()
    return engine


class TestToolNamesConstant:
    def test_task_tool_names_excludes_task_find_and_task_manage(self) -> None:
        assert "task_find" not in TASK_TOOL_NAMES
        assert "task_manage" not in TASK_TOOL_NAMES

    def test_task_tool_names_includes_workflow_tools(self) -> None:
        assert "workflow_find" in TASK_TOOL_NAMES
        assert "workflow_manage" in TASK_TOOL_NAMES
        assert "sdk_reference" in TASK_TOOL_NAMES


class TestRegisterTaskTools:
    def test_only_workflow_tools_are_registered(self) -> None:
        registry = ToolRegistry()
        context = make_context()
        engine = _make_engine()

        register_task_tools(registry, engine, context)

        names = registry.names()
        assert "workflow_find" in names
        assert "workflow_manage" in names
        assert "sdk_reference" in names
        assert "task_find" not in names
        assert "task_manage" not in names

    def test_toolset_group_is_registered(self) -> None:
        registry = ToolRegistry()
        context = make_context()
        engine = _make_engine()

        register_task_tools(registry, engine, context)

        assert "tasks" in registry._groups

    def test_exactly_three_tools_registered(self) -> None:
        registry = ToolRegistry()
        context = make_context()
        engine = _make_engine()

        register_task_tools(registry, engine, context)

        names = registry.names()
        registered_task_tools = [n for n in names if n in TASK_TOOL_NAMES]
        assert len(registered_task_tools) == 3

    def test_codegen_deps_passed_to_workflow_manage(self) -> None:
        """When graph_provider is available, workflow_manage should receive
        code_store, version_store, and workflow_builder so it can run codegen."""
        registry = ToolRegistry()
        context = make_context()
        engine = _make_engine()

        register_task_tools(registry, engine, context)

        tool = registry.resolve_by_name("workflow_manage")
        assert hasattr(tool, "_code_store")
        assert hasattr(tool, "_version_store")
        assert hasattr(tool, "_workflow_builder")

    def test_no_codegen_deps_without_graph_provider(self) -> None:
        registry = ToolRegistry()
        context = make_context(graph_provider=None)
        engine = _make_engine()

        register_task_tools(registry, engine, context)

        tool = registry.resolve_by_name("workflow_manage")
        assert tool._code_store is None
        assert tool._version_store is None
        assert tool._workflow_builder is None

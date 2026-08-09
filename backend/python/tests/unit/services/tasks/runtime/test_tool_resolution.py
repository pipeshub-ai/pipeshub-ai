"""`TaskSpecAssembler._resolve_tool_names` decides what authority a headless
run gets, so it is tested on its own: the surrounding `assemble()` needs a
graph provider, a config service and a real LLM, none of which change the
resolution decision.

Behaviour: tools that were available in the interactive chat context but
not in the headless executor's registry are **dropped with a warning**
rather than blocking the run. Only when *every* requested tool is
unresolvable does the method raise `ToolResolutionError` (failing the run
is better than granting a random set of tools).
"""
from __future__ import annotations

import logging

import pytest

from app.services.tasks.domain.errors import PrerequisiteError, ToolResolutionError
from app.services.tasks.domain.models import TaskDefinition, TaskPrincipal
from app.services.tasks.runtime.spec_assembler import TaskSpecAssembler


class _FakeRegistry:
    """Only `names()` is read by `_resolve_tool_names`."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def names(self) -> list[str]:
        return list(self._names)


def _make_task(**overrides) -> TaskDefinition:
    defaults = {
        "org_id": "org-1",
        "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Weekly research",
        "description": "research the market",
        "instructions": "Search the web and summarize",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


def _resolve(task: TaskDefinition, registry_names: list[str]) -> list[str]:
    return TaskSpecAssembler._resolve_tool_names(
        task, _FakeRegistry(registry_names), logging.getLogger(__name__),
    )


class TestAllToolsUnresolvable:
    """When every declared tool is missing, the run MUST fail."""

    def test_single_missing_tool_fails(self) -> None:
        task = _make_task(tool_names=["web_search"])
        with pytest.raises(ToolResolutionError) as exc:
            _resolve(task, ["dynamic__web_search", "search_knowledge_base", "get_record"])
        assert exc.value.missing == ["web_search"]

    def test_the_error_names_the_closest_available_tool(self) -> None:
        task = _make_task(tool_names=["web_search"])
        with pytest.raises(ToolResolutionError) as exc:
            _resolve(task, ["dynamic__web_search", "get_record"])
        assert exc.value.suggestions["web_search"] == ["dynamic__web_search"]
        assert "dynamic__web_search" in str(exc.value)

    def test_it_is_a_prerequisite_error_so_the_executor_will_not_retry(self) -> None:
        task = _make_task(tool_names=["nope"])
        with pytest.raises(PrerequisiteError):
            _resolve(task, ["dynamic__web_search"])

    def test_multiple_missing_reported_at_once(self) -> None:
        task = _make_task(tool_names=["web_search", "slack_post"])
        with pytest.raises(ToolResolutionError) as exc:
            _resolve(task, ["get_record"])
        assert set(exc.value.missing) == {"web_search", "slack_post"}


class TestPartialResolution:
    """When some tools resolve and others don't, the run proceeds with
    the resolvable subset (the missing ones are interactive-only tools
    like knowledgegraph__search or run_code)."""

    def test_partial_resolution_drops_missing_tools(self) -> None:
        task = _make_task(tool_names=["web_search", "slack_post", "get_record"])
        resolved = _resolve(task, ["get_record"])
        assert resolved == ["get_record"]

    def test_partial_resolution_preserves_order(self) -> None:
        task = _make_task(tool_names=["dynamic__web_search", "run_code", "get_record"])
        resolved = _resolve(task, ["dynamic__web_search", "get_record"])
        assert resolved == ["dynamic__web_search", "get_record"]


class TestResolvableTools:
    def test_declared_tools_resolve_to_exactly_themselves(self) -> None:
        task = _make_task(tool_names=["dynamic__web_search"])
        resolved = _resolve(task, ["dynamic__web_search", "dynamic__fetch_url", "get_record"])
        assert resolved == ["dynamic__web_search"]

    def test_no_declared_tools_means_the_session_default(self) -> None:
        task = _make_task(tool_names=[])
        resolved = _resolve(task, ["dynamic__web_search", "get_record"])
        assert resolved == ["dynamic__web_search", "get_record"]

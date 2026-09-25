"""The system prompt the default builder assembles every turn.

Deep-mode sub-agents get their prompt from this builder, and PipesHub's own
builder reuses its skills section, so what it includes decides what the
model knows: the goal and its requirements, the live task list, which tool
groups can be opened on demand, and which skills exist.

Uses a real `ToolRegistry` and a real `SkillManager` over skill files on
disk; nothing is stubbed.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.agent_loop_lib.agent.prompt import build_system_prompt, render_skills_overview
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal, Todo, TodoStatus
from app.agent_loop_lib.modules.providers.skills.filesystem_index import (
    FilesystemSkillIndex,
)
from app.agent_loop_lib.modules.providers.skills.filesystem_store import (
    FilesystemSkillStore,
)
from app.agent_loop_lib.modules.providers.skills.in_memory_tracker import (
    InMemoryUsageTracker,
)
from app.agent_loop_lib.modules.providers.skills.manager import (
    SkillManager,
    SkillManagerConfig,
)
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from pathlib import Path


class _Named(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def path(self) -> str:
        return f"/toolsets/test/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data="")


def _spec(**overrides: object) -> AgentSpec:
    fields: dict[str, object] = {
        "name": "sub-agent",
        "system_prompt": "You look things up in Jira.",
        "model": ModelSpec(provider="scripted", model="scripted-model"),
    }
    fields.update(overrides)
    return AgentSpec(**fields)


def _write_skill(root: Path, name: str, description: str, category: str | None = None) -> None:
    skill_dir = root / name
    os.makedirs(skill_dir, exist_ok=True)
    meta = f"metadata:\n  agent-loop:\n    category: {category}\n" if category else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{meta}---\n\nBody.\n", encoding="utf-8",
    )


async def _skills(root: Path, **config: object) -> SkillManager:
    manager = SkillManager(
        store=FilesystemSkillStore(str(root)),
        index=FilesystemSkillIndex(str(root)),
        tracker=InMemoryUsageTracker(),
        config=SkillManagerConfig(skills_dir=str(root), **config),
    )
    await manager.start()
    return manager


class TestGoalAndTasks:
    def test_goal_requirements_criteria_and_constraints_are_all_stated(self) -> None:
        goal = Goal(
            description="List my open tickets",
            requirements=["only project OPS"],
            success_criteria=["one line per ticket"],
            constraints=["read-only"],
        )

        prompt = build_system_prompt(_spec(), AgentRuntime(), goal)

        assert prompt.startswith("You look things up in Jira.")
        assert (
            "Goal: List my open tickets\nRequirements: only project OPS\n"
            "Success criteria: one line per ticket\nConstraints: read-only"
        ) in prompt

    def test_task_list_shows_each_items_progress(self) -> None:
        todos = [
            Todo(content="Find tickets", status=TodoStatus.COMPLETED),
            Todo(content="Group by owner", status=TodoStatus.IN_PROGRESS),
            Todo(content="Write summary", status=TodoStatus.PENDING),
        ]

        prompt = build_system_prompt(_spec(), AgentRuntime(), Goal(description="g"), todos)

        assert "Current task list:\n[x] Find tickets\n[~] Group by owner\n[ ] Write summary" in prompt

    def test_run_sections_override_spec_sections_of_the_same_name(self) -> None:
        spec = _spec(extra_prompt_sections={"note": "from the spec", "keep": "kept"})

        prompt = build_system_prompt(spec, AgentRuntime(), Goal(description="g"), [], {"note": "from this run"})

        assert "from this run" in prompt
        assert "from the spec" not in prompt
        assert "kept" in prompt

    def test_a_custom_builder_replaces_the_default_entirely(self) -> None:
        class _Fixed:
            def build(self, spec, runtime, goal, todos, extra_sections) -> str:
                return f"custom for {goal.description} with {len(todos)} todos"

        prompt = build_system_prompt(_spec(system_prompt=_Fixed()), AgentRuntime(), Goal(description="x"))

        assert prompt == "custom for x with 0 todos"


class TestToolGroupOverview:
    def _registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        for name in ("jira_search", "jira_get", "slack_post"):
            registry.register_tool(_Named(name))
        registry.register_toolset("connectors", "Connected apps", [])
        registry.register_toolset("jira", "Jira issues", ["jira_search", "jira_get"], parent="connectors")
        registry.register_toolset("slack", "Slack messages", ["slack_post"], parent="connectors")
        return registry

    def test_lazy_disclosure_lists_groups_as_a_nested_tree(self) -> None:
        runtime = AgentRuntime(tool_registry=self._registry())

        prompt = build_system_prompt(_spec(tool_disclosure="lazy"), runtime, Goal(description="g"))

        assert "Call list_toolsets(toolset)" in prompt
        assert prompt.endswith(
            "\n- connectors: Connected apps (3 tool(s))\n"
            "  - jira: Jira issues (2 tool(s))\n"
            "  - slack: Slack messages (1 tool(s))"
        )

    def test_eager_disclosure_never_advertises_groups(self) -> None:
        runtime = AgentRuntime(tool_registry=self._registry())

        prompt = build_system_prompt(_spec(), runtime, Goal(description="g"))

        assert "list_toolsets" not in prompt


class TestSkillsOverview:
    async def test_small_catalogue_lists_each_skill_with_its_first_sentence(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "pdf-report", "Builds a PDF report. Uses the sandbox.")
        _write_skill(tmp_path, "csv-clean", "Cleans CSV files")
        runtime = AgentRuntime(skills=await _skills(tmp_path))

        overview = render_skills_overview(runtime)

        assert overview.startswith("## Skills\n")
        assert "2 skill(s) available via `load_skill(name)`." in overview
        lines = overview.splitlines()
        assert lines.index("- **csv-clean**: Cleans CSV files") < lines.index("- **pdf-report**: Builds a PDF report")

    async def test_large_catalogue_is_summarised_by_category(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "pdf-report", "Builds PDFs", category="documents")
        _write_skill(tmp_path, "docx-report", "Builds Word files", category="documents")
        _write_skill(tmp_path, "k8s-debug", "Debugs pods", category="devops")
        _write_skill(tmp_path, "misc", "Anything else")
        runtime = AgentRuntime(skills=await _skills(tmp_path, catalog_render_limit=3))

        overview = render_skills_overview(runtime)

        assert "4 skills available, grouped by category." in overview
        assert overview.endswith("**Categories:**\n- devops (1)\n- documents (2)\n- uncategorized (1)")
        assert "pdf-report" not in overview

    async def test_no_skills_means_no_section(self, tmp_path: Path) -> None:
        assert render_skills_overview(AgentRuntime(skills=await _skills(tmp_path))) == ""
        assert render_skills_overview(AgentRuntime()) == ""

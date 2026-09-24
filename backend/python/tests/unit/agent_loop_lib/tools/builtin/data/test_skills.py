"""`LoadSkillTool` — the level-2 progressive-disclosure tool — must resolve
and stage a loaded skill's bundle (its own `SKILL.md` plus every bundled
resource, and transitively every skill it `requires`) via the injected
`SkillBundleResolver`/`SkillMaterializer` (see `bundle.py`) so the next
fresh coding sandbox receives them at `skills/<name>/<path>` (see
`input_staging.py`'s `add_staged_skill_resources` and the builtin
`office-utils`/`docx`/`pptx` packs this exists for)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.core.types import ToolResult
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillInUseError,
    SkillMetadata,
)
from app.agent_loop_lib.modules.providers.skills.bundle import SkillBundle
from app.agent_loop_lib.modules.providers.skills.loader import render_skill_md
from app.agent_loop_lib.tools.builtin.data.skills import (
    LoadSkillResourceTool,
    LoadSkillTool,
    SkillManageTool,
    SkillSearchTool,
    SkillsListTool,
)
from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    peek_staged_skill_resources,
)


def _tool_result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="tool", content=content, is_error=is_error)


@pytest.fixture(autouse=True)
def _reset_staged_skill_resources():
    token = input_staging._staged_skill_resources.set(None)
    yield
    input_staging._staged_skill_resources.reset(token)


def _skill(name: str, *, requires: list[str] | None = None, resources: dict[str, list[str]] | None = None) -> Skill:
    return Skill(
        metadata=SkillMetadata(name=name, description=f"use {name}", requires=requires or []),
        body=f"# {name}\n\ninstructions",
        resources=resources or {},
    )


def _skill_md_bytes(skill: Skill) -> bytes:
    return render_skill_md(skill).encode("utf-8")


class _FakeSkillManager:
    """Implements just the surface `SkillBundleResolver`/`LoadSkillTool`
    depend on: `activate_skill` (full body) and `get_resources` (bulk read,
    mirroring `SkillManager.get_resources` -> `SkillReader.get_resources`)."""

    def __init__(self, skills: dict[str, Skill], resources: dict[str, dict[str, str]] | None = None) -> None:
        self._skills = skills
        self._resources = resources or {}

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        skill = self._skills.get(name)
        if skill is None:
            raise RegistryError(f"Skill {name!r} not found")
        return skill

    async def get_resources(self, name: str) -> dict[str, str]:
        return dict(self._resources.get(name, {}))


class TestLoadSkillStagesResources:
    async def test_stages_the_skills_own_skill_md_even_with_no_bundled_resources(self) -> None:
        """SKILL.md itself always travels with the bundle (fixes problem 6
        in the plan: a skill whose scripts read sibling files relative to
        SKILL.md, or that tells the model to `cat` a reference, has
        something to read) — a "no resources" skill is not a staging no-op."""
        skill = _skill("docx")
        manager = _FakeSkillManager({"docx": skill})
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="docx")

        assert result.success
        assert peek_staged_skill_resources() == {"skills/docx/SKILL.md": _skill_md_bytes(skill)}

    async def test_stages_the_skills_own_resources(self) -> None:
        skill = _skill("office-utils", resources={"scripts": ["scripts/unpack.py", "scripts/pack.py"]})
        manager = _FakeSkillManager(
            {"office-utils": skill},
            {"office-utils": {"scripts/unpack.py": "print('unpack')", "scripts/pack.py": "print('pack')"}},
        )
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="office-utils")

        assert result.success
        staged = peek_staged_skill_resources()
        assert staged == {
            "skills/office-utils/SKILL.md": _skill_md_bytes(skill),
            "skills/office-utils/scripts/unpack.py": b"print('unpack')",
            "skills/office-utils/scripts/pack.py": b"print('pack')",
        }

    async def test_stages_transitively_required_skills_resources(self) -> None:
        office_utils = _skill("office-utils", resources={"scripts": ["scripts/unpack.py"]})
        docx = _skill("docx", requires=["office-utils"])
        manager = _FakeSkillManager(
            {"office-utils": office_utils, "docx": docx},
            {"office-utils": {"scripts/unpack.py": "print('unpack')"}},
        )
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="docx")

        assert result.success
        staged = peek_staged_skill_resources()
        assert staged == {
            "skills/docx/SKILL.md": _skill_md_bytes(docx),
            "skills/office-utils/SKILL.md": _skill_md_bytes(office_utils),
            "skills/office-utils/scripts/unpack.py": b"print('unpack')",
        }

    async def test_requires_cycle_does_not_infinite_loop(self) -> None:
        a = _skill("a", requires=["b"], resources={"scripts": ["scripts/a.py"]})
        b = _skill("b", requires=["a"], resources={"scripts": ["scripts/b.py"]})
        manager = _FakeSkillManager(
            {"a": a, "b": b},
            {"a": {"scripts/a.py": "a"}, "b": {"scripts/b.py": "b"}},
        )
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="a")

        assert result.success
        staged = peek_staged_skill_resources()
        assert staged == {
            "skills/a/SKILL.md": _skill_md_bytes(a),
            "skills/a/scripts/a.py": b"a",
            "skills/b/SKILL.md": _skill_md_bytes(b),
            "skills/b/scripts/b.py": b"b",
        }

    async def test_missing_required_skill_is_skipped_not_fatal(self) -> None:
        docx = _skill("docx", requires=["missing-skill"])
        manager = _FakeSkillManager({"docx": docx})
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="docx")

        assert result.success
        assert result.data["name"] == "docx"
        assert peek_staged_skill_resources() == {"skills/docx/SKILL.md": _skill_md_bytes(docx)}

    async def test_unknown_skill_returns_error_without_staging(self) -> None:
        manager = _FakeSkillManager({})
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="nonexistent")

        assert result.success
        assert "error" in result.data
        assert peek_staged_skill_resources() is None

    async def test_output_includes_sandbox_root_not_root_dir(self) -> None:
        skill = _skill("docx")
        manager = _FakeSkillManager({"docx": skill})
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="docx")

        assert result.data["sandbox_root"] == "skills/docx"
        assert "root_dir" not in result.data
        assert "working directory" in result.data["sandbox_note"]


class TestLoadSkillToolInjectedDependencies:
    """`LoadSkillTool` depends on the `SkillMaterializer` protocol, not a
    concrete implementation — it must not import `input_staging` at all
    (Dependency Inversion, and the seam a future `VfsSkillMaterializer`
    swaps into via `skills_wiring.register_skill_tools`)."""

    async def test_does_not_import_input_staging_module(self) -> None:
        import app.agent_loop_lib.tools.builtin.data.skills as skills_module

        assert not hasattr(skills_module, "add_staged_skill_resources")

    async def test_uses_the_injected_materializer_instead_of_the_default(self) -> None:
        skill = _skill("office-utils", resources={"scripts": ["scripts/unpack.py"]})
        manager = _FakeSkillManager(
            {"office-utils": skill}, {"office-utils": {"scripts/unpack.py": "print('unpack')"}},
        )

        class _RecordingMaterializer:
            def __init__(self) -> None:
                self.materialized: list[SkillBundle] = []

            async def materialize(self, bundles: list[SkillBundle]) -> None:
                self.materialized.extend(bundles)

        materializer = _RecordingMaterializer()
        tool = LoadSkillTool(manager, materializer=materializer)

        await tool.execute(name="office-utils")

        assert peek_staged_skill_resources() is None  # default StagingSkillMaterializer never ran
        assert [b.name for b in materializer.materialized] == ["office-utils"]
        assert materializer.materialized[0].mount_path == "skills/office-utils"
        assert materializer.materialized[0].files["scripts/unpack.py"] == b"print('unpack')"


class TestSkillManageToolDeleteGuard:
    async def test_in_use_delete_returns_error_output(self) -> None:
        manager = AsyncMock()
        manager.delete = AsyncMock(side_effect=SkillInUseError(
            "pdf-extractor",
            used_by_agents=[{"id": "a1", "name": "Support Bot"}],
            required_by_skills=[],
        ))
        tool = SkillManageTool(manager)
        result = await tool.execute(action="delete", name="pdf-extractor")
        assert result.success is False
        assert "pdf-extractor" in (result.error or "")
        assert "agent" in (result.error or "").lower()


class TestSkillToolSummaries:
    """`display_name`/`summarize_args`/`summarize_result` are what let old
    (pre-summary) and new persisted `MessagePart`s render sensible chat
    timeline labels without a schema change — see the "Skill activity UX"
    plan. The one invariant that matters most: a skill's full Markdown
    `body` (or a resource file's `content`) must never leak into a
    summary, since those are exactly what these summaries exist to hide
    from the collapsed timeline."""

    def test_load_skill_args_and_result_summaries(self) -> None:
        tool = LoadSkillTool(manager=object())
        assert tool.display_name == "Loaded skill"
        assert tool.summarize_args({"name": "docx"}) == "Loading skill docx"

        body = "# docx\n\nvery long instructions " * 50
        result = _tool_result({"name": "docx", "body": body, "resources": {}})
        summary = tool.summarize_result({"name": "docx"}, result)
        assert summary == "Loaded skill docx"
        assert body not in summary

    def test_load_skill_result_summary_flags_deprecation(self) -> None:
        tool = LoadSkillTool(manager=object())
        result = _tool_result({"name": "old-skill", "body": "...", "deprecated": True})
        assert tool.summarize_result({"name": "old-skill"}, result) == "Loaded skill old-skill (deprecated)"

    def test_load_skill_result_summary_surfaces_unknown_skill_without_body(self) -> None:
        tool = LoadSkillTool(manager=object())
        result = _tool_result({"error": "Unknown skill: 'nope'."})
        assert tool.summarize_result({"name": "nope"}, result) == "Unknown skill: 'nope'."

    def test_load_skill_result_summary_surfaces_execution_error(self) -> None:
        tool = LoadSkillTool(manager=object())
        result = _tool_result("boom", is_error=True)
        assert tool.summarize_result({"name": "docx"}, result) == "boom"

    def test_load_skill_resource_summaries_never_contain_file_content(self) -> None:
        tool = LoadSkillResourceTool(manager=object())
        assert tool.display_name == "Loaded skill file"
        assert tool.summarize_args({"name": "docx", "path": "scripts/unpack.py"}) == "Loading docx/scripts/unpack.py"

        file_body = "print('unpack')" * 100
        result = _tool_result({"name": "docx", "path": "scripts/unpack.py", "content": file_body})
        summary = tool.summarize_result({"name": "docx", "path": "scripts/unpack.py"}, result)
        assert summary == "Loaded docx/scripts/unpack.py"
        assert file_body not in summary

    def test_skill_search_summaries(self) -> None:
        tool = SkillSearchTool(manager=object())
        assert tool.display_name == "Searched skills"
        assert tool.summarize_args({"query": "pdf"}) == 'Searching skills: "pdf"'

        result = _tool_result({"matches": [{"name": "pdf-extractor"}, {"name": "docx"}]})
        summary = tool.summarize_result({"query": "pdf"}, result)
        assert summary == 'Found 2 skills matching "pdf"'

    def test_skill_search_result_summary_singular(self) -> None:
        tool = SkillSearchTool(manager=object())
        result = _tool_result({"matches": [{"name": "docx"}]})
        assert tool.summarize_result({"query": "docx"}, result) == 'Found 1 skill matching "docx"'

    def test_skills_list_summaries(self) -> None:
        tool = SkillsListTool(manager=object())
        assert tool.display_name == "Listed skills"
        assert tool.summarize_args({}) == "Listing skills"
        assert tool.summarize_args({"category": "devops"}) == "Listing skills (category=devops)"

        result = _tool_result({"skills": [{"name": "docx"}, {"name": "pptx"}], "count": 2})
        assert tool.summarize_result({}, result) == "Found 2 skills"

    def test_skill_manage_create_summaries(self) -> None:
        tool = SkillManageTool(manager=object())
        assert tool.display_name == "Managed skill"
        args = {"action": "create", "name": "docx", "body": "long body " * 50}
        assert tool.summarize_args(args) == "Creating skill docx"

        result = _tool_result({"name": "docx", "created": True, "category": None})
        summary = tool.summarize_result(args, result)
        assert summary == "Created skill docx"
        assert "long body" not in summary

    def test_skill_manage_rollback_and_health_summaries(self) -> None:
        tool = SkillManageTool(manager=object())

        rollback_args = {"action": "rollback", "name": "docx", "version": "v2"}
        rollback_result = _tool_result({"name": "docx", "rolled_back_to": "v2", "version": "v2"})
        assert tool.summarize_result(rollback_args, rollback_result) == "Rolled back skill docx to v2"

        health_args = {"action": "health", "name": "docx"}
        health_result = _tool_result({"name": "docx", "recommended_action": "keep", "reason": "used often"})
        assert tool.summarize_result(health_args, health_result) == "Skill docx health check: keep"

    def test_skill_manage_result_summary_surfaces_execution_error(self) -> None:
        tool = SkillManageTool(manager=object())
        args = {"action": "delete", "name": "docx"}
        result = _tool_result("Skill 'docx' not found, or lives in a read-only root", is_error=True)
        assert tool.summarize_result(args, result) == "Skill 'docx' not found, or lives in a read-only root"


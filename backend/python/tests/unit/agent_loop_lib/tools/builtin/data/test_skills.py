"""`LoadSkillTool` — the level-2 progressive-disclosure tool — must stage
a loaded skill's bundled resources (and, transitively, every skill it
`requires`) so the next fresh coding sandbox receives them at
`skills/<name>/<path>` (see `input_staging.py`'s `add_staged_skill_resources`
and the builtin `office-utils`/`docx`/`pptx` packs this exists for)."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import Skill, SkillMetadata
from app.agent_loop_lib.tools.builtin.data.skills import LoadSkillTool
from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import peek_staged_skill_resources


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


class _FakeSkillManager:
    def __init__(self, skills: dict[str, Skill], resources: dict[tuple[str, str], str]) -> None:
        self._skills = skills
        self._resources = resources

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        skill = self._skills.get(name)
        if skill is None:
            raise RegistryError(f"Skill {name!r} not found")
        return skill

    async def load_resource(self, name: str, path: str) -> str:
        content = self._resources.get((name, path))
        if content is None:
            raise RegistryError(f"Resource {path!r} not found for skill {name!r}")
        return content


class TestLoadSkillStagesResources:
    async def test_no_resources_is_a_no_op(self) -> None:
        manager = _FakeSkillManager({"docx": _skill("docx")}, {})
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="docx")

        assert result.success
        assert peek_staged_skill_resources() is None

    async def test_stages_the_skills_own_resources(self) -> None:
        skill = _skill("office-utils", resources={"scripts": ["scripts/unpack.py", "scripts/pack.py"]})
        manager = _FakeSkillManager(
            {"office-utils": skill},
            {
                ("office-utils", "scripts/unpack.py"): "print('unpack')",
                ("office-utils", "scripts/pack.py"): "print('pack')",
            },
        )
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="office-utils")

        assert result.success
        staged = peek_staged_skill_resources()
        assert staged == {
            "skills/office-utils/scripts/unpack.py": b"print('unpack')",
            "skills/office-utils/scripts/pack.py": b"print('pack')",
        }

    async def test_stages_transitively_required_skills_resources(self) -> None:
        office_utils = _skill("office-utils", resources={"scripts": ["scripts/unpack.py"]})
        docx = _skill("docx", requires=["office-utils"])
        manager = _FakeSkillManager(
            {"office-utils": office_utils, "docx": docx},
            {("office-utils", "scripts/unpack.py"): "print('unpack')"},
        )
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="docx")

        assert result.success
        staged = peek_staged_skill_resources()
        assert staged == {"skills/office-utils/scripts/unpack.py": b"print('unpack')"}

    async def test_requires_cycle_does_not_infinite_loop(self) -> None:
        a = _skill("a", requires=["b"], resources={"scripts": ["scripts/a.py"]})
        b = _skill("b", requires=["a"], resources={"scripts": ["scripts/b.py"]})
        manager = _FakeSkillManager(
            {"a": a, "b": b},
            {("a", "scripts/a.py"): "a", ("b", "scripts/b.py"): "b"},
        )
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="a")

        assert result.success
        staged = peek_staged_skill_resources()
        assert staged == {
            "skills/a/scripts/a.py": b"a",
            "skills/b/scripts/b.py": b"b",
        }

    async def test_missing_required_skill_is_skipped_not_fatal(self) -> None:
        docx = _skill("docx", requires=["missing-skill"])
        manager = _FakeSkillManager({"docx": docx}, {})
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="docx")

        assert result.success
        assert result.data["name"] == "docx"

    async def test_unknown_skill_returns_error_without_staging(self) -> None:
        manager = _FakeSkillManager({}, {})
        tool = LoadSkillTool(manager)

        result = await tool.execute(name="nonexistent")

        assert result.success
        assert "error" in result.data
        assert peek_staged_skill_resources() is None

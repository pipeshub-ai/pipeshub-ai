"""`SkillBundleResolver`/`SkillMaterializer` (`bundle.py`) — the VFS-ready
seam between "what a skill's bundled files are" and "how they become
visible to executing code". Today's `StagingSkillMaterializer` and any
future `VfsSkillMaterializer` must satisfy the same contract, exercised
here via a shared parametrized fixture."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import Skill, SkillMetadata
from app.agent_loop_lib.modules.providers.skills.bundle import (
    SkillBundle,
    SkillBundleResolver,
    StagingSkillMaterializer,
    skill_mount_path,
)
from app.agent_loop_lib.modules.providers.skills.loader import render_skill_md
from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    peek_staged_skill_resources,
)


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


class TestSkillMountPath:
    def test_mount_path_is_skills_slash_name(self) -> None:
        assert skill_mount_path("office-utils") == "skills/office-utils"


class TestSkillBundleAsSandboxFiles:
    def test_prefixes_every_file_with_mount_path(self) -> None:
        bundle = SkillBundle(
            name="office-utils",
            mount_path="skills/office-utils",
            files={"SKILL.md": b"---\n", "scripts/unpack.py": b"print(1)"},
        )

        assert bundle.as_sandbox_files() == {
            "skills/office-utils/SKILL.md": b"---\n",
            "skills/office-utils/scripts/unpack.py": b"print(1)",
        }


class TestSkillBundleResolver:
    async def test_resolves_a_single_skill_including_skill_md(self) -> None:
        skill = _skill("office-utils", resources={"scripts": ["scripts/unpack.py"]})
        manager = _FakeSkillManager({"office-utils": skill}, {"office-utils": {"scripts/unpack.py": "print(1)"}})
        resolver = SkillBundleResolver(manager)

        bundles = await resolver.resolve("office-utils")

        assert len(bundles) == 1
        assert bundles[0].name == "office-utils"
        assert bundles[0].mount_path == "skills/office-utils"
        assert bundles[0].files == {
            "SKILL.md": _skill_md_bytes(skill),
            "scripts/unpack.py": b"print(1)",
        }

    async def test_follows_requires_transitively(self) -> None:
        office_utils = _skill("office-utils", resources={"scripts": ["scripts/unpack.py"]})
        docx = _skill("docx", requires=["office-utils"])
        manager = _FakeSkillManager(
            {"office-utils": office_utils, "docx": docx},
            {"office-utils": {"scripts/unpack.py": "print(1)"}},
        )
        resolver = SkillBundleResolver(manager)

        bundles = await resolver.resolve("docx")

        assert [b.name for b in bundles] == ["docx", "office-utils"]

    async def test_requires_cycle_resolves_each_skill_exactly_once(self) -> None:
        a = _skill("a", requires=["b"])
        b = _skill("b", requires=["a"])
        manager = _FakeSkillManager({"a": a, "b": b})
        resolver = SkillBundleResolver(manager)

        bundles = await resolver.resolve("a")

        assert sorted(b.name for b in bundles) == ["a", "b"]

    async def test_unknown_skill_resolves_to_no_bundles(self) -> None:
        manager = _FakeSkillManager({})
        resolver = SkillBundleResolver(manager)

        bundles = await resolver.resolve("nonexistent")

        assert bundles == []

    async def test_missing_required_skill_is_skipped_not_fatal(self) -> None:
        docx = _skill("docx", requires=["missing"])
        manager = _FakeSkillManager({"docx": docx})
        resolver = SkillBundleResolver(manager)

        bundles = await resolver.resolve("docx")

        assert [b.name for b in bundles] == ["docx"]


class _RecordingMaterializer:
    """Fake `SkillMaterializer` used to prove the contract test below
    exercises the interface, not `StagingSkillMaterializer`'s internals."""

    def __init__(self) -> None:
        self.materialized: list[SkillBundle] = []

    async def materialize(self, bundles: list[SkillBundle]) -> None:
        self.materialized.extend(bundles)

    def sandbox_files(self) -> dict[str, bytes]:
        merged: dict[str, bytes] = {}
        for bundle in self.materialized:
            merged.update(bundle.as_sandbox_files())
        return merged


@pytest.mark.parametrize(
    "materializer_factory",
    [StagingSkillMaterializer, _RecordingMaterializer],
    ids=["StagingSkillMaterializer", "RecordingMaterializer"],
)
class TestSkillMaterializerContract:
    """Any `SkillMaterializer` implementation — today's staging adapter or
    tomorrow's VFS mount — must make the exact same file set addressable
    at the exact same `skills/<name>/<path>` keys. Add a future
    `VfsSkillMaterializer` to this parametrize list, not a new test."""

    async def test_materialize_makes_every_bundle_file_addressable(self, materializer_factory) -> None:
        bundle = SkillBundle(
            name="office-utils",
            mount_path="skills/office-utils",
            files={"SKILL.md": b"---\n", "scripts/unpack.py": b"print(1)"},
        )
        materializer = materializer_factory()

        await materializer.materialize([bundle])

        if isinstance(materializer, StagingSkillMaterializer):
            observed = peek_staged_skill_resources()
        else:
            observed = materializer.sandbox_files()

        assert observed == {
            "skills/office-utils/SKILL.md": b"---\n",
            "skills/office-utils/scripts/unpack.py": b"print(1)",
        }

    async def test_materialize_of_empty_bundle_list_is_a_no_op(self, materializer_factory) -> None:
        materializer = materializer_factory()

        await materializer.materialize([])

        if isinstance(materializer, StagingSkillMaterializer):
            assert peek_staged_skill_resources() is None
        else:
            assert materializer.sandbox_files() == {}

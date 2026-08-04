"""Spec-conformance gate for `app/agents/agent_loop/skills/builtin_packs/`
(see the plan's "Compatibility" section): every builtin pack must be a
plain, valid agentskills.io SKILL.md that survives
`parse_skill_md -> render_skill_md -> parse_skill_md` losslessly and
passes `SkillValidator.validate_skill()` — the same gate a pack pushed to
GitHub and installed into Claude Code/OpenClaw/skills.sh would have to
clear."""

from __future__ import annotations

import os

import pytest

from app.agent_loop_lib.modules.providers.skills.base import SkillSource
from app.agent_loop_lib.modules.providers.skills.loader import (
    load_skills_from_dir,
    parse_skill_md,
    render_skill_md,
)
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator

_PACKS_ROOT = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "app", "agents", "agent_loop", "skills", "builtin_packs",
)

_EXPECTED_PACK_NAMES = {
    "office-utils", "docx", "pptx", "xlsx", "pdf",
    "file-conversion", "data-analysis", "data-visualization",
}


def _load_packs():
    return load_skills_from_dir(os.path.abspath(_PACKS_ROOT))


class TestPackDiscovery:
    def test_every_expected_pack_is_present(self) -> None:
        names = {skill.name for skill in _load_packs()}
        assert names == _EXPECTED_PACK_NAMES


class TestValidatorGuard:
    @pytest.mark.parametrize("skill", _load_packs(), ids=lambda s: s.name)
    def test_passes_validate_skill(self, skill) -> None:
        SkillValidator().validate_skill(skill, expected_name=skill.name)

    @pytest.mark.parametrize("skill", _load_packs(), ids=lambda s: s.name)
    def test_is_tagged_builtin(self, skill) -> None:
        assert skill.metadata.source == SkillSource.BUILTIN

    @pytest.mark.parametrize("skill", _load_packs(), ids=lambda s: s.name)
    def test_has_pack_provenance(self, skill) -> None:
        assert skill.metadata.pack_name == skill.name
        assert skill.metadata.pack_version


class TestRoundTrip:
    @pytest.mark.parametrize("skill", _load_packs(), ids=lambda s: s.name)
    def test_render_then_reparse_is_lossless(self, skill) -> None:
        reparsed = parse_skill_md(render_skill_md(skill), expected_name=skill.name)
        assert reparsed.metadata == skill.metadata
        assert reparsed.body == skill.body


class TestRequiresGraphIsResolvable:
    def test_every_requires_entry_points_at_a_real_pack(self) -> None:
        packs = _load_packs()
        names = {skill.name for skill in packs}
        for skill in packs:
            for required in skill.metadata.requires:
                assert required in names, f"{skill.name!r} requires unknown skill {required!r}"

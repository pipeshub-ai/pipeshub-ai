"""Regression coverage for the fix-lazy-skills-gap todo: with lazy toolset
disclosure on and a skill manager configured, the "skills" toolset
(skills_list/load_skill/load_skill_resource/skill_search/skill_manage) must
be visible from turn 0 — a skill needs to be checked BEFORE the model
improvises its first move, not discovered after a `fetch_tools("skills")`
round-trip it has no particular reason to make on its own.
"""

from __future__ import annotations

from app.agent_loop_lib.agent.tool_loop import initial_visible_tools
from app.agent_loop_lib.control_plane.config import ControlPlaneConfig, SkillManagerConfig
from app.agent_loop_lib.control_plane.control_plane import ControlPlane

# `ASSISTANT_ROLE.allowed_tools` only grants `load_skill`/`skill_search` of
# the five skill tools — `spec.tool_names` is a permission ceiling
# `initial_visible_tools` intersects against (see its docstring), so those
# are the two this role can actually see pinned, regardless of how many the
# toolset itself contains.
_ASSISTANT_GRANTED_SKILL_TOOLS = {"load_skill", "skill_search"}


class TestSkillsToolsetPinnedAtTurnZero:
    async def test_skills_toolset_auto_pinned_when_skill_manager_enabled(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            skill_manager=SkillManagerConfig(skills_dir=str(tmp_path)),
            hooks=[], tools=[],
        )
        cp = ControlPlane(cfg)
        await cp.start()

        assert "skills" in cfg.lazy_toolsets.pinned_toolsets

        spec = cp.make_spec("assistant")
        assert "skills" in spec.pinned_toolsets
        visible = initial_visible_tools(spec, cp.runtime)
        assert _ASSISTANT_GRANTED_SKILL_TOOLS <= visible

        # Unpinned (default `pinned_toolsets=[]`), the same two tools would
        # instead be deferred behind a fetch_tools("skills") round-trip —
        # this is the exact gap the fix closes.
        spec_without_pin = spec.model_copy(update={"pinned_toolsets": []})
        visible_without_pin = initial_visible_tools(spec_without_pin, cp.runtime)
        assert not (_ASSISTANT_GRANTED_SKILL_TOOLS & visible_without_pin)

    async def test_skills_toolset_not_pinned_when_skill_manager_disabled(self, tmp_path) -> None:
        """No `skills_dir` -> the skill manager (and its toolset) is never
        wired at all, so nothing should end up in `pinned_toolsets` either."""
        cfg = ControlPlaneConfig(hooks=[], tools=[])
        cp = ControlPlane(cfg)
        await cp.start()

        assert "skills" not in cfg.lazy_toolsets.pinned_toolsets

    async def test_does_not_duplicate_across_repeated_starts_or_explicit_config(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            skill_manager=SkillManagerConfig(skills_dir=str(tmp_path)),
            lazy_toolsets={"pinned_toolsets": ["skills", "web_search"]},
            hooks=[], tools=[],
        )
        cp = ControlPlane(cfg)
        await cp.start()

        assert cfg.lazy_toolsets.pinned_toolsets.count("skills") == 1
        assert "web_search" in cfg.lazy_toolsets.pinned_toolsets

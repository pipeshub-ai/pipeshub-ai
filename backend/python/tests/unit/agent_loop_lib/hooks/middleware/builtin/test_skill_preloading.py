"""`skill_preloading` (PRE_AGENT middleware) — relevance-band thresholds
(full body vs. pointer vs. nothing) and its no-op guards (no scope, no
goal description, empty catalog, search failure)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.builtin.skill_preloading import skill_preloading
from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillMatch,
    SkillMetadata,
)


@dataclass
class _FakeSpec:
    tool_names: list[str] | None = None


@dataclass
class _FakeScope:
    extra_prompt_sections: dict[str, str] = field(default_factory=dict)
    spec: _FakeSpec = field(default_factory=_FakeSpec)


def _skill(name: str) -> Skill:
    metadata = SkillMetadata(name=name, description=f"Use {name} when needed")
    return Skill(metadata=metadata, body=f"Full instructions for {name}.", root_dir="/tmp")


class _FakeSkillManager:
    """Test double for `SkillManager`: only the surface `skill_preloading`
    actually calls (`catalog_snapshot`, `search`, `activate_skill`)."""

    def __init__(self, matches: list[SkillMatch], *, catalog_non_empty: bool = True, search_raises: bool = False) -> None:
        self._matches = matches
        # Deliberately independent of `matches` — an empty SEARCH result
        # for this goal (nothing relevant enough) is a different scenario
        # from an empty CATALOG (nothing to search at all), and the
        # middleware short-circuits differently for each (see
        # `test_no_op_when_catalog_is_empty` vs.
        # `test_stale_preloaded_section_is_cleared_when_nothing_matches`).
        self._catalog_non_empty = catalog_non_empty
        self._search_raises = search_raises
        self.search_calls: list[str] = []
        self.activated: list[str] = []

    def catalog_snapshot(self) -> list[SkillMetadata]:
        if not self._catalog_non_empty:
            return []
        return [m.skill for m in self._matches] or [_skill("some-other-skill").metadata]

    async def search(self, query: str, limit: int = 10) -> list[SkillMatch]:
        self.search_calls.append(query)
        if self._search_raises:
            raise RuntimeError("search backend unavailable")
        return self._matches

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        self.activated.append(name)
        return _skill(name)


async def _run(manager: _FakeSkillManager, *, goal_description: str | None = "deploy the service", scope: Any = "__default__", **kwargs) -> AgentLifecycleContext:
    middleware = skill_preloading(manager, **kwargs)
    goal = Goal(description=goal_description) if goal_description is not None else None
    if scope == "__default__":
        scope = _FakeScope()
    ctx = AgentLifecycleContext(goal=goal, scope=scope)

    called = {"next": False}

    async def next_fn() -> None:
        called["next"] = True

    await middleware(ctx, next_fn)
    assert called["next"], "middleware must always call next_fn(), even on no-op paths"
    return ctx


class TestRelevanceBands:
    async def test_above_body_threshold_injects_full_body_and_activates(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="semantic match")]
        manager = _FakeSkillManager(matches)

        ctx = await _run(manager, preload_body_threshold=0.75, mention_threshold=0.4)

        section = ctx.scope.extra_prompt_sections["preloaded_skills"]
        assert "Full instructions for deploy-service." in section
        assert "already been loaded in full" in section
        assert manager.activated == ["deploy-service"]

    async def test_between_thresholds_injects_pointer_only_no_activation(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.5, match_reason="semantic match")]
        manager = _FakeSkillManager(matches)

        ctx = await _run(manager, preload_body_threshold=0.75, mention_threshold=0.4)

        section = ctx.scope.extra_prompt_sections["preloaded_skills"]
        assert "deploy-service: Use deploy-service when needed" in section
        assert "may also be relevant" in section
        assert "Full instructions" not in section
        assert manager.activated == []

    async def test_below_mention_threshold_injects_nothing(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.2, match_reason="weak match")]
        manager = _FakeSkillManager(matches)

        ctx = await _run(manager, preload_body_threshold=0.75, mention_threshold=0.4)

        assert "preloaded_skills" not in ctx.scope.extra_prompt_sections

    async def test_mixed_relevance_bands_produce_both_sections(self) -> None:
        matches = [
            SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="strong"),
            SkillMatch(skill=_skill("rollback-service").metadata, relevance=0.5, match_reason="medium"),
            SkillMatch(skill=_skill("unrelated-skill").metadata, relevance=0.1, match_reason="weak"),
        ]
        manager = _FakeSkillManager(matches)

        ctx = await _run(manager, preload_body_threshold=0.75, mention_threshold=0.4)

        section = ctx.scope.extra_prompt_sections["preloaded_skills"]
        assert "Full instructions for deploy-service." in section
        assert "rollback-service: Use rollback-service when needed" in section
        assert "unrelated-skill" not in section

    async def test_activation_failure_falls_back_to_pointer(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="strong")]
        manager = _FakeSkillManager(matches)

        async def _raise(*args: Any, **kwargs: Any) -> Skill:
            raise RuntimeError("store unavailable")

        manager.activate_skill = _raise  # type: ignore[assignment]

        ctx = await _run(manager, preload_body_threshold=0.75, mention_threshold=0.4)

        section = ctx.scope.extra_prompt_sections["preloaded_skills"]
        assert "deploy-service: Use deploy-service when needed" in section
        assert "Full instructions" not in section


class TestScopedAgentWithoutLoadSkill:
    """A `domain_agents.py`-style child whose `tool_names` is an explicit
    allowlist that omits `load_skill` cannot act on a bare mention-tier
    pointer — this band must collapse to nothing for it, while the
    body-tier band (which requires no follow-up tool call) is unaffected."""

    async def test_mention_tier_pointer_is_suppressed_without_load_skill(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.5, match_reason="medium")]
        manager = _FakeSkillManager(matches)
        scope = _FakeScope(spec=_FakeSpec(tool_names=["run_code", "install_packages"]))

        ctx = await _run(manager, scope=scope, preload_body_threshold=0.75, mention_threshold=0.4)

        assert "preloaded_skills" not in ctx.scope.extra_prompt_sections

    async def test_body_tier_still_injected_without_load_skill(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="strong")]
        manager = _FakeSkillManager(matches)
        scope = _FakeScope(spec=_FakeSpec(tool_names=["run_code", "install_packages"]))

        ctx = await _run(manager, scope=scope, preload_body_threshold=0.75, mention_threshold=0.4)

        section = ctx.scope.extra_prompt_sections["preloaded_skills"]
        assert "Full instructions for deploy-service." in section
        assert manager.activated == ["deploy-service"]

    async def test_activation_failure_drops_silently_without_load_skill(self) -> None:
        """Same activation failure as `test_activation_failure_falls_back_to_pointer`,
        but for a scope that can't act on the fallback pointer either — the
        match must disappear entirely rather than leaving a dead-end pointer."""
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="strong")]
        manager = _FakeSkillManager(matches)

        async def _raise(*args: Any, **kwargs: Any) -> Skill:
            raise RuntimeError("store unavailable")

        manager.activate_skill = _raise  # type: ignore[assignment]
        scope = _FakeScope(spec=_FakeSpec(tool_names=["run_code"]))

        ctx = await _run(manager, scope=scope, preload_body_threshold=0.75, mention_threshold=0.4)

        assert "preloaded_skills" not in ctx.scope.extra_prompt_sections

    async def test_load_skill_present_in_explicit_allowlist_still_gets_pointers(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.5, match_reason="medium")]
        manager = _FakeSkillManager(matches)
        scope = _FakeScope(spec=_FakeSpec(tool_names=["load_skill", "skill_search"]))

        ctx = await _run(manager, scope=scope, preload_body_threshold=0.75, mention_threshold=0.4)

        section = ctx.scope.extra_prompt_sections["preloaded_skills"]
        assert "deploy-service: Use deploy-service when needed" in section


class TestNoOpGuards:
    async def test_no_op_when_scope_is_none(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="strong")]
        manager = _FakeSkillManager(matches)
        await _run(manager, scope=None)  # must not raise

    async def test_no_op_when_goal_has_no_description(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="strong")]
        manager = _FakeSkillManager(matches)
        ctx = await _run(manager, goal_description="")
        assert "preloaded_skills" not in ctx.scope.extra_prompt_sections
        assert manager.search_calls == []

    async def test_no_op_when_catalog_is_empty(self) -> None:
        manager = _FakeSkillManager([], catalog_non_empty=False)
        ctx = await _run(manager)
        assert "preloaded_skills" not in ctx.scope.extra_prompt_sections
        assert manager.search_calls == []

    async def test_search_failure_is_swallowed(self) -> None:
        matches = [SkillMatch(skill=_skill("deploy-service").metadata, relevance=0.9, match_reason="strong")]
        manager = _FakeSkillManager(matches, search_raises=True)
        ctx = await _run(manager)  # must not raise
        assert "preloaded_skills" not in ctx.scope.extra_prompt_sections

    async def test_stale_preloaded_section_is_cleared_when_nothing_matches(self) -> None:
        manager = _FakeSkillManager([], catalog_non_empty=True)
        scope = _FakeScope(extra_prompt_sections={"preloaded_skills": "stale from a prior turn"})
        ctx = await _run(manager, scope=scope)
        assert "preloaded_skills" not in ctx.scope.extra_prompt_sections

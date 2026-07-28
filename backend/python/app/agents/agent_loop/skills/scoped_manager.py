"""`ScopedSkillManager`: a per-agent read-scoping decorator over
`SkillManager` (Open/Closed — `agent_loop_lib` itself is untouched; this is
purely an adapter-layer wrapper, same spirit as `GraphSkillStore`'s
`visibility_scope` but for a DIFFERENT axis: "which skills is THIS agent
allowed to see this turn" rather than "which skills did THIS user create").

Only wraps the read/activation surface the prompt builder and the 5 skill
tools actually walk through (`catalog_snapshot`/`list_skills`/`search`/
`activate_skill`/`load_resource`) — everything else (create/update/delete/
versions/health/usage tracking) passes straight through via `__getattr__`
delegation, since assignment scoping is a "what can this agent see/load"
concern, not a governance one; an agent that can call `skill_manage` at all
already has the same write authority it always did.

Assignment semantics (see the plan's Phase 4): an agent with an EMPTY
`allowed_names` set has no explicit assignment and sees the full upstream
catalog unfiltered (today's behavior, unchanged) — wrapping only ever
narrows, so `PipesHubAgentFactory` only bothers constructing this wrapper
when `context.agent_skills` is non-empty (see `skills_wiring.build_skill_manager`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.modules.providers.skills.base import (
        Skill,
        SkillFilter,
        SkillMatch,
        SkillMetadata,
        SkillSource,
        SkillStatus,
    )
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager

__all__ = ["ScopedSkillManager"]


class ScopedSkillManager:
    def __init__(self, manager: "SkillManager", allowed_names: set[str]) -> None:
        self._manager = manager
        self._allowed = set(allowed_names)

    def __getattr__(self, item: str) -> Any:  # noqa: ANN401 - transparent delegation
        return getattr(self._manager, item)

    def _visible(self, name: str) -> bool:
        return name in self._allowed

    # ---- Tier 1: catalog -----------------------------------------------

    def catalog_snapshot(self) -> "list[SkillMetadata]":
        return [m for m in self._manager.catalog_snapshot() if self._visible(m.name)]

    async def list_skills(self, filter: "SkillFilter | None" = None) -> "list[SkillMetadata]":
        metadatas = await self._manager.list_skills(filter)
        return [m for m in metadatas if self._visible(m.name)]

    async def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
        subcategory: str | None = None,
        tags: "list[str] | None" = None,
        status: "SkillStatus | None" = None,
        source: "SkillSource | None" = None,
        limit: int = 10,
    ) -> "list[SkillMatch]":
        # Over-fetch, then filter — the underlying index doesn't know about
        # per-agent scoping, so a naive `limit` passthrough could return
        # fewer than `limit` visible matches even when more exist.
        matches = await self._manager.search(
            query, category=category, subcategory=subcategory, tags=tags,
            status=status, source=source, limit=max(limit * 4, limit),
        )
        return [m for m in matches if self._visible(m.skill.name)][:limit]

    # ---- Tier 2/3: activation + resources --------------------------------

    async def activate_skill(self, name: str, session_id: str | None = None) -> "Skill":
        from app.agent_loop_lib.core.exceptions import RegistryError

        if not self._visible(name):
            raise RegistryError(f"Skill {name!r} is not assigned to this agent")
        return await self._manager.activate_skill(name, session_id)

    async def load_resource(self, name: str, path: str) -> str:
        from app.agent_loop_lib.core.exceptions import RegistryError

        if not self._visible(name):
            raise RegistryError(f"Skill {name!r} is not assigned to this agent")
        return await self._manager.load_resource(name, path)

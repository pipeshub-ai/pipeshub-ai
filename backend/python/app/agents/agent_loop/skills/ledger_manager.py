"""`LedgerSkillManager`: the run's skill manager, which tells the request's
label ledger about every skill whose name, description, body or files it hands
back, with where that skill came from (`SkillOrigin`).

What the manager returns is what can reach a prompt: the skills overview and
the preloaded skills in the system prompt, the five skill tools, and the files
`load_skill` stages for code. Labelling at this one surface follows the skills
themselves, however a run reaches them, and a new path that reads through the
manager is labelled without anyone listing it. It wraps the run's outermost
manager (after `ScopedSkillManager`), so it sees only what the run can see.

`GraphSkillStore` records each skill's origin in `SkillOrigins` as it reads or
creates it; a name with no record counts as a person's own. Writes pass
straight through. `catalog_size()` counts the catalog for a log line without
telling the ledger, since no model is given it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.agent_loop_lib.modules.providers.skills.base import SkillInUseError
from app.modules.agents.privacy.ledger import SkillOrigin, note_skills

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.agent_loop_lib.modules.providers.skills.base import (
        Skill,
        SkillFilter,
        SkillMatch,
        SkillMetadata,
        SkillVersionInfo,
    )

__all__ = ["LedgerSkillManager", "SkillOrigins"]


_RESTRICTS: Final[dict[SkillOrigin, int]] = {
    SkillOrigin.BUILTIN: 0, SkillOrigin.RUN: 0, SkillOrigin.AGENT: 1, SkillOrigin.PERSON: 2,
}


class SkillOrigins:
    """Where each skill a run's store read or created came from, by name. A name keeps the most
    restrictive origin of any document read under it: the catalog can still list a document the
    name no longer holds, and what the run was shown under the name came from one of them."""

    def __init__(self) -> None:
        self._of: dict[str, SkillOrigin] = {}

    def record(self, name: str, origin: SkillOrigin) -> None:
        held = self._of.get(name)
        if held is None or _RESTRICTS[origin] > _RESTRICTS[held]:
            self._of[name] = origin

    def of(self, name: str) -> SkillOrigin:
        return self._of.get(name, SkillOrigin.PERSON)


class LedgerSkillManager:
    def __init__(self, manager: Any, origins: SkillOrigins, *, agent_key: str | None = None) -> None:  # noqa: ANN401
        """`agent_key`: the run's agent, which a skill assigned to it belongs to; None when the run
        has no saved agent."""
        self._manager = manager
        self._origins = origins
        self._agent_key = agent_key

    def __getattr__(self, item: str) -> Any:  # noqa: ANN401 - transparent delegation
        return getattr(self._manager, item)

    def _shown(self, names: Iterable[str]) -> None:
        note_skills(((name, self._origins.of(name)) for name in names), agent_key=self._agent_key)

    def catalog_size(self) -> int:
        return len(self._manager.catalog_snapshot())

    # ---- what the run is shown ------------------------------------------

    def catalog_snapshot(self) -> list[SkillMetadata]:
        catalog = self._manager.catalog_snapshot()
        self._shown(m.name for m in catalog)
        return catalog

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:  # noqa: A002
        metadatas = await self._manager.list_skills(filter)
        self._shown(m.name for m in metadatas)
        return metadatas

    async def search(self, query: str = "", **kwargs: Any) -> list[SkillMatch]:  # noqa: ANN401
        matches = await self._manager.search(query, **kwargs)
        self._shown(m.skill.name for m in matches)
        return matches

    async def get_skill(self, name: str) -> Skill:
        skill = await self._manager.get_skill(name)
        self._shown([name])
        return skill

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        skill = await self._manager.activate_skill(name, session_id)
        self._shown([name])
        return skill

    async def load_resource(self, name: str, path: str) -> str:
        content = await self._manager.load_resource(name, path)
        self._shown([name])
        return content

    async def get_resources(self, name: str) -> dict[str, str]:
        resources = await self._manager.get_resources(name)
        self._shown([name])
        return resources

    async def list_versions(self, name: str) -> list[SkillVersionInfo]:
        versions = await self._manager.list_versions(name)
        if versions:
            self._shown([name])
        return versions

    async def get_version(self, name: str, version: str) -> Skill | None:
        skill = await self._manager.get_version(name, version)
        if skill is not None:
            self._shown([name])
        return skill

    async def evaluate_skill_health(self, name: str) -> tuple[str, str]:
        health = await self._manager.evaluate_skill_health(name)
        self._shown([name])
        return health

    async def delete(self, name: str, *, detach: bool = False) -> bool:
        try:
            return await self._manager.delete(name, detach=detach)
        except SkillInUseError:
            # Its message names the agents and skills that use this one, as the asker may see them.
            note_skills([(name, SkillOrigin.PERSON)], agent_key=self._agent_key)
            raise

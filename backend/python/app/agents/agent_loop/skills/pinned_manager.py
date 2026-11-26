"""`PinnedSkillManager`: the durable loop's skill reads of a pinned skill, at the content admission pinned (FB-11).

Wraps the run's `SkillManager` (or its `ScopedSkillManager`) the way `ScopedSkillManager` wraps the manager: the
agent-facing reads of a pinned name (`activate_skill`, `load_resource`, `get_resources`, and the metadata the
catalog, `skills_list` and `skill_search` show) come from `PinnedSkills`, the reader engines resolve bundles from
too. An edit since admission never reaches the run; a deletion, disabling or unassignment stops the skill at the
next read. `get_skill` stays the management lookup of what is stored now, so `skill_manage` and skill learning
keep acting on the current skill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md
from app.agents.agent_loop.skills.graph_store import (
    _resource_listing,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.skills import SKILL_ENTRYPOINT, SkillBundleError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agent_loop_lib.modules.providers.skills.base import (
        Skill,
        SkillFilter,
        SkillMatch,
        SkillMetadata,
    )
    from app.agents.agent_loop.skills.bundle_source import PinnedSkills

__all__ = ["PinnedSkillManager"]


class PinnedSkillManager:
    def __init__(self, manager: Any, pins: PinnedSkills, digests: Mapping[str, str]) -> None:  # noqa: ANN401 - a SkillManager or its scoped wrapper
        self._manager = manager
        self._pins = pins
        self._digests = dict(digests)
        self._shown: dict[str, SkillMetadata] = {}

    def __getattr__(self, item: str) -> Any:  # noqa: ANN401 - transparent delegation
        return getattr(self._manager, item)

    async def _pinned(self, name: str) -> tuple[Skill, dict[str, str]] | None:
        digest = self._digests.get(name)
        if digest is None:
            return None
        try:
            files = await self._pins.files(name, digest)
        except SkillBundleError as exc:
            raise RegistryError(f"Skill {name!r} is no longer available to this run") from exc
        texts = {path: content.decode("utf-8") for path, content in files.items()}
        skill = parse_skill_md(texts.pop(SKILL_ENTRYPOINT, ""), expected_name=name)
        return skill.model_copy(update={"resources": _resource_listing(texts)}), texts

    async def prime(self) -> None:
        """The pinned metadata the catalog shows this attempt; a skill that no longer holds keeps today's entry,
        since the attempt's item load refuses it anyway."""
        for name in self._digests:
            try:
                pinned = await self._pinned(name)
            except RegistryError:
                continue
            if pinned is not None:
                self._shown[name] = pinned[0].metadata

    def _show(self, metadata: SkillMetadata) -> SkillMetadata:
        return self._shown.get(metadata.name, metadata)

    def catalog_snapshot(self) -> list[SkillMetadata]:
        return [self._show(m) for m in self._manager.catalog_snapshot()]

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:  # noqa: A002 - the manager's signature
        return [self._show(m) for m in await self._manager.list_skills(filter)]

    async def search(self, query: str = "", **kwargs: Any) -> list[SkillMatch]:  # noqa: ANN401 - the manager's filters
        matches = await self._manager.search(query, **kwargs)
        return [m.model_copy(update={"skill": self._show(m.skill)}) for m in matches]

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        pinned = await self._pinned(name)
        if pinned is None:
            return await self._manager.activate_skill(name, session_id)
        if session_id is not None:
            await self._manager.record_activation(name, session_id)
        return pinned[0]

    async def load_resource(self, name: str, path: str) -> str:
        pinned = await self._pinned(name)
        if pinned is None:
            return await self._manager.load_resource(name, path)
        content = pinned[1].get(path)
        if content is None:
            raise RegistryError(f"Resource {path!r} not found for skill {name!r}")
        return content

    async def get_resources(self, name: str) -> dict[str, str]:
        pinned = await self._pinned(name)
        return pinned[1] if pinned is not None else await self._manager.get_resources(name)

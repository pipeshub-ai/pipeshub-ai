from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillFilter,
    SkillMatch,
    SkillMetadata,
)

"""Search/indexing ABC — kept separate from `SkillStore` (SOLID — Single
Responsibility) so a future `VectorSkillIndex` (embedding similarity) can be
swapped in via DI without touching how skills are actually stored, and so a
read-only consumer that only needs `list_skills`/`get_skill` never has to
depend on indexing internals at all.
"""


class SkillIndex(ABC):
    @abstractmethod
    async def rebuild(self, skills: list[SkillMetadata]) -> None:
        """Rebuild the index from a full metadata list (called by
        `SkillManager.start()`/`refresh()` after (re)scanning the store)."""

    @abstractmethod
    async def search(
        self, query: str, filter: SkillFilter | None = None, limit: int = 10,
    ) -> list[SkillMatch]:
        ...

    @abstractmethod
    async def get_categories(self) -> dict[str, list[str]]:
        """category -> sorted list of subcategories seen under it (an entry
        with no subcategories maps to an empty list)."""

    @abstractmethod
    async def get_tags(self) -> list[str]:
        ...

    @abstractmethod
    async def add_entry(self, metadata: SkillMetadata) -> None:
        ...

    @abstractmethod
    async def remove_entry(self, name: str) -> None:
        ...

    @abstractmethod
    async def update_entry(self, metadata: SkillMetadata) -> None:
        ...

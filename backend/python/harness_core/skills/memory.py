"""In-memory ``SkillContentSource``: each publish adds a version; old versions and deleted skills stay readable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.skills.types import SkillRef


@dataclass
class _History:
    versions: dict[str, dict[str, bytes]] = field(default_factory=dict[str, dict[str, bytes]])
    current: str | None = None


class InMemorySkillContentSource:
    def __init__(self) -> None:
        self._skills: dict[tuple[str, str], _History] = {}

    def publish(self, tenant_id: str, name: str, files: Mapping[str, bytes], *, version: str | None = None) -> str:
        """Make ``files`` the skill's current content; returns its version (``1``, ``2``, ... by default)."""
        history = self._skills.setdefault((tenant_id, name), _History())
        assigned = version or str(len(history.versions) + 1)
        history.versions[assigned] = dict(files)
        history.current = assigned
        return assigned

    def delete(self, tenant_id: str, name: str) -> None:
        history = self._skills.get((tenant_id, name))
        if history is not None:
            history.current = None

    async def files(self, tenant_id: str, skill: SkillRef) -> Mapping[str, bytes] | None:
        history = self._skills.get((tenant_id, skill.name))
        if history is None:
            return None
        version = skill.version if skill.version is not None else history.current
        found = history.versions.get(version) if version is not None else None
        return dict(found) if found is not None else None

    async def versions(self, tenant_id: str, name: str) -> Sequence[str]:
        history = self._skills.get((tenant_id, name))
        return tuple(history.versions) if history is not None else ()


__all__ = ["InMemorySkillContentSource"]

from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillCandidate,
    SkillFilter,
    SkillMetadata,
    SkillVersionInfo,
)

"""Storage ABCs for skills (SOLID — Interface Segregation): read-only
consumers (e.g. a future read-only catalog UI, the prompt renderer's
underlying data source) depend on `SkillReader` alone; only the
`SkillManager`'s write path and `skill_manage` tool need the full
`SkillWriter` surface. Concrete backends (filesystem today; SQLite, S3, a
hosted skill hub tomorrow) implement `SkillStore` = both combined.
"""


class SkillReader(ABC):
    @abstractmethod
    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:
        """All skill metadata matching `filter` (None = everything)."""

    @abstractmethod
    async def get_skill(self, name: str) -> Skill | None:
        """Full skill (metadata + body + resource listing), or None if unknown."""

    @abstractmethod
    async def get_resource(self, skill_name: str, resource_path: str) -> str | None:
        """Contents of a bundled resource file (relative to the skill's root
        dir, e.g. 'scripts/deploy.sh'), or None if the skill or file doesn't
        exist."""

    @abstractmethod
    async def exists(self, name: str) -> bool:
        ...


class SkillWriter(ABC):
    @abstractmethod
    async def create_skill(
        self, name: str, content: str, category: str | None = None, subcategory: str | None = None,
    ) -> SkillMetadata:
        """Create a new skill from a full SKILL.md `content` string (YAML
        frontmatter + Markdown body) under `category`/`subcategory` (both
        optional — an uncategorized skill lands at the primary root's top
        level). Raises `SkillFormatError` on spec violations, `RegistryError`
        if a skill with this name already exists."""

    @abstractmethod
    async def update_skill(self, name: str, content: str) -> SkillMetadata:
        """Overwrite an existing skill's full SKILL.md content in place
        (same category/location). Raises `RegistryError` if unknown."""

    @abstractmethod
    async def patch_skill(self, name: str, old_string: str, new_string: str) -> bool:
        """Targeted find-and-replace against the skill's body (mirrors
        `edit_file`'s ergonomics) — `old_string` must match exactly once.
        Returns False if the skill is unknown or `old_string` isn't found."""

    @abstractmethod
    async def delete_skill(self, name: str) -> bool:
        """Hard-delete a skill and its directory. Returns False if unknown."""

    @abstractmethod
    async def write_resource(self, skill_name: str, path: str, content: str) -> bool:
        """Write (create or overwrite) a bundled resource file under the
        skill's `scripts/`, `references/`, or `assets/` subdirectory."""

    @abstractmethod
    async def remove_resource(self, skill_name: str, path: str) -> bool:
        ...

    @abstractmethod
    async def deprecate_skill(self, name: str, reason: str, replaced_by: str | None = None) -> bool:
        """Mark a skill DEPRECATED in place (never deletes it) so in-flight
        references degrade gracefully — see `SkillManager.activate_skill`."""


class SkillStore(SkillReader, SkillWriter, ABC):
    """Combined read+write surface — what concrete backends implement and
    what `SkillManager` depends on."""


class SkillHistoryReader(ABC):
    """Optional version-history surface (Interface Segregation): a store
    backed by durable storage-level versioning (e.g. a graph DB's revision
    collection) implements this ADDITIONALLY to `SkillStore`; `FilesystemSkillStore`
    does not, and `SkillManager` degrades gracefully (see `manager.py`'s
    `list_versions`/`get_version`/`rollback`) when it isn't implemented.
    Versioning is therefore a *storage* concern, never something
    `SkillManager` computes itself.
    """

    @abstractmethod
    async def list_versions(self, name: str) -> list[SkillVersionInfo]:
        """Every prior + current revision of `name`, newest first. Empty
        list if the skill is unknown or has no recorded history yet."""

    @abstractmethod
    async def get_version(self, name: str, version: str) -> Skill | None:
        """The full `Skill` as of a specific `version` string, or None if
        that skill/version combination doesn't exist."""

    @abstractmethod
    async def rollback(self, name: str, version: str) -> SkillMetadata:
        """Restore `name` to the content of a prior `version` — implemented
        as a new revision (the current content is itself snapshotted first),
        never a destructive overwrite of history. Raises `RegistryError` if
        the skill or version is unknown."""


class SkillCandidateStore(ABC):
    """Optional durable queue for learning-loop candidates pending
    governance review (Interface Segregation, mirrors `SkillHistoryReader`).
    `SkillManager` falls back to its own filesystem-JSON queue
    (`_meta/candidates/`) when the wired store doesn't implement this —
    see `manager.py`."""

    @abstractmethod
    async def queue_candidate(self, candidate: SkillCandidate) -> None:
        ...

    @abstractmethod
    async def get_pending_candidates(self) -> list[SkillCandidate]:
        ...

    @abstractmethod
    async def remove_candidate(self, candidate_id: str) -> None:
        ...

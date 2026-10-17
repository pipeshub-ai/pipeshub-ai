from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillCandidate,
    SkillFilter,
    SkillMetadata,
    SkillReferentialUsage,
    SkillStatus,
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

    async def get_resources(self, name: str) -> dict[str, str]:
        """Bulk read of every bundled resource file's contents for `name` —
        `{relative_path: content}`. Default implementation loops over
        `get_resource` once per path in the skill's resource listing (one
        `get_skill` call plus N single-resource reads); a store that can do
        this in a single read (e.g. `GraphSkillStore`, off one already-
        fetched document) should override for efficiency — this is exactly
        the N+1 `SkillBundleResolver` exists to avoid. Empty dict if the
        skill is unknown."""
        skill = await self.get_skill(name)
        if skill is None:
            return {}
        paths = [path for paths in skill.resources.values() for path in paths]
        result: dict[str, str] = {}
        for path in paths:
            content = await self.get_resource(name, path)
            if content is not None:
                result[path] = content
        return result

    async def get_referential_usage(self, name: str) -> SkillReferentialUsage:
        """Agents assigned this skill and other skills that `requires` it.
        Default is empty (filesystem / in-memory stores have no agent
        graph). `GraphSkillStore` overrides."""
        return SkillReferentialUsage()


class SkillWriter(ABC):
    @abstractmethod
    async def create_skill(
        self, name: str, content: str, category: str | None = None, subcategory: str | None = None,
        resources: dict[str, str] | None = None,
    ) -> SkillMetadata:
        """Create a new skill from a full SKILL.md `content` string (YAML
        frontmatter + Markdown body) under `category`/`subcategory` (both
        optional — an uncategorized skill lands at the primary root's top
        level). `resources`, when given, persists a `{relative_path:
        content}` map of bundled files alongside the skill in the SAME
        write (e.g. `BuiltinSkillSeeder` seeding a pack's scripts/
        references/assets) — `None` means no bundled resources, matching
        today's behavior. Raises `SkillFormatError` on spec violations
        (including an invalid resource path — see
        `SkillValidator.validate_resource_path`), `RegistryError` if a
        skill with this name already exists."""

    @abstractmethod
    async def update_skill(
        self,
        name: str,
        content: str,
        resources: dict[str, str] | None = None,
        expected_updated_at: int | None = None,
    ) -> SkillMetadata:
        """Overwrite an existing skill's full SKILL.md content in place
        (same category/location). `resources`, when given, REPLACES the
        skill's entire bundled resource set (not a merge); `None` (the
        default) keeps whatever resources already exist unchanged. Raises
        `RegistryError` if unknown.

        `expected_updated_at`, when given, is the `updatedAtTimestamp` the
        caller last observed — a mismatch raises `SkillConflictError`.
        `None` (the default) is last-write-wins (tools, CLI). A store
        without mutation timestamps (filesystem) may ignore it."""

    @abstractmethod
    async def patch_skill(
        self,
        name: str,
        old_string: str,
        new_string: str,
        expected_updated_at: int | None = None,
    ) -> bool:
        """Targeted find-and-replace against the skill's body (mirrors
        `edit_file`'s ergonomics) — `old_string` must match exactly once.
        Returns False if the skill is unknown or `old_string` isn't found.
        `expected_updated_at` has the same meaning as on `update_skill`."""

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

    @abstractmethod
    async def set_skill_status(
        self,
        name: str,
        status: SkillStatus,
        from_status: SkillStatus | None = None,
    ) -> bool:
        """Write `status` onto an existing skill in place — the enable/
        disable primitive. Deliberately NOT `update_skill`: a mute/unmute is
        not a content edit, so it must not snapshot a version revision or
        bump semver the way `deprecate_skill` (which reuses `update_skill`
        for its content-carrying reason/replaced_by) does.

        When `from_status` is set, compare it to the persisted status and
        write `status` only if they match, in one atomic operation where
        the backend allows. Returns False if the skill is unknown or that
        comparison fails (a transition conflict). `from_status=None` is
        last-write-wins."""

    async def detach_from_agents(self, name: str) -> None:
        """Remove `AGENT_HAS_SKILL` edges pointing at `name`. No-op on
        stores that don't persist those edges."""
        return


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

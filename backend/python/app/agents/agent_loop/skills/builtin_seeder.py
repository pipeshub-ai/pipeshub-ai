"""`BuiltinSkillSeeder` — the local-only first cut of the future npm
`SkillPackInstaller`: resolve a pack -> validate each SKILL.md via
`SkillValidator` -> bulk-import via the store's create/update, with
provenance stamped. Here "resolve" is `load_skills_from_dir(packs_root)`
(the in-repo `builtin_packs/` directory) instead of an npm tarball fetch —
upgrading to a real installer later is swapping the resolver, not
rebuilding the pipeline (see the plan's Phase 4 note).

**Per-org seeded copies, not a shared global row**: each org gets its own
`agentSkills` doc per builtin skill, created under a dedicated system
identity (`SEED_IDENTITY`) distinct from any real user — this is what lets
`_is_unmodified` tell "seeded, never touched" apart from "a human or the
skill_writer sub-agent edited this" using the exact same `updatedBy` field
`GraphSkillStore` already stamps on every write. The moment an org edits a
builtin-sourced skill, `updatedBy` becomes their id and this seeder never
silently overwrites it again on a later pack upgrade.

Callers must pass a `GraphSkillStore` constructed with
`user_id=SEED_IDENTITY` (see `skills_wiring.py`) — NOT the store used for
the rest of that request's skill operations, which is bound to the real
acting user. Reusing the real-user store here would stamp every seeded
skill's `createdBy` as that user, making it indistinguishable from a
human-authored one and defeating fork-detection for the very next user in
that org.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.modules.providers.skills.base import Skill, SkillFilter, SkillSource
from app.agent_loop_lib.modules.providers.skills.loader import load_skills_from_dir, render_skill_md
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator

if TYPE_CHECKING:
    from app.agents.agent_loop.skills.graph_store import GraphSkillStore

__all__ = ["BuiltinSkillSeeder", "SEED_IDENTITY"]

logger = logging.getLogger(__name__)

SEED_IDENTITY = "system:builtin-seeder"


class BuiltinSkillSeeder:
    def __init__(self, packs_root: str, *, validator: SkillValidator | None = None) -> None:
        validator = validator or SkillValidator()
        self._packs: list[Skill] = load_skills_from_dir(packs_root)
        if not self._packs:
            # Zero packs means the SKILL.md files didn't ship with the build
            # (e.g. a `**/*.md` .dockerignore rule stripping them from the
            # image) — without this guard the seeder is a silent no-op and
            # every org just sees an empty catalog with nothing in the logs.
            raise RuntimeError(
                f"BuiltinSkillSeeder: no skill packs found under {packs_root!r} — "
                "the builtin SKILL.md files are missing from this deployment "
                "(packaging/COPY problem?)"
            )
        for skill in self._packs:
            # Fails process startup loudly on a broken in-repo pack rather
            # than silently skipping it forever — this is our own content,
            # not a third-party skill `load_skills_from_dir` is right to be
            # lenient about.
            validator.validate_skill(skill, expected_name=skill.metadata.name)

    @property
    def pack_versions(self) -> dict[str, str]:
        """`{name: pack_version}` snapshot of the in-repo packs — the cheap
        pre-check callers use to skip a sync round-trip entirely when an
        org's catalog is already current (see `skills_wiring.py`)."""
        return {
            skill.metadata.name: (skill.metadata.pack_version or skill.metadata.version)
            for skill in self._packs
        }

    async def sync(self, store: "GraphSkillStore") -> None:
        """Idempotent by construction: `create_skill`/`update_skill` are
        upserts keyed by `{org_id}_{name}` (`graph_store.py`'s org-isolation
        tests already cover this), so concurrent seed attempts from
        parallel requests converge safely without needing a lock. Every
        write still goes through `SkillValidator`, gets a real revision
        snapshot in `agentSkillVersions`, and is audit-logged by
        `AuditGovernor` when it's layered on top — builtin skills are
        first-class citizens of the versioning/governance machinery
        already built, not a special case."""
        existing = {
            m.name: m
            for m in await store.list_skills(SkillFilter(source=SkillSource.BUILTIN))
        }
        for skill in self._packs:
            current = existing.get(skill.metadata.name)
            if current is None:
                await self._create(store, skill)
            elif current.pack_version != skill.metadata.pack_version:
                await self._maybe_upgrade(store, skill, current.pack_version)
            # else: already at the current pack version — nothing to do.

    async def _create(self, store: "GraphSkillStore", skill: Skill) -> None:
        try:
            await store.create_skill(
                skill.metadata.name, render_skill_md(skill),
                skill.metadata.category, skill.metadata.subcategory,
            )
            logger.info("builtin_seeder: seeded %r (pack v%s)", skill.metadata.name, skill.metadata.pack_version)
        except Exception:
            logger.exception("builtin_seeder: failed to seed builtin skill %r", skill.metadata.name)

    async def _maybe_upgrade(self, store: "GraphSkillStore", skill: Skill, current_pack_version: str | None) -> None:
        if not await self._is_unmodified(store, skill.metadata.name):
            logger.info(
                "builtin_seeder: %r has org edits (pack v%s -> v%s available) — skipping auto-upgrade",
                skill.metadata.name, current_pack_version, skill.metadata.pack_version,
            )
            return
        try:
            await store.update_skill(skill.metadata.name, render_skill_md(skill))
            logger.info(
                "builtin_seeder: upgraded %r from pack v%s to v%s",
                skill.metadata.name, current_pack_version, skill.metadata.pack_version,
            )
        except Exception:
            logger.exception("builtin_seeder: failed to upgrade builtin skill %r", skill.metadata.name)

    async def _is_unmodified(self, store: "GraphSkillStore", name: str) -> bool:
        provenance = await store.get_provenance(name)
        if provenance is None:
            return True
        return provenance.get("updated_by") in (None, SEED_IDENTITY)

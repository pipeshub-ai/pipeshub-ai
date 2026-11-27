"""Release catalog items for the built-in skill packs (01-hld §5.1), one per pack, by bundle digest."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.agents.agent_loop.skills.builtin_seeder import BuiltinSkillSeeder
from app.control_plane.catalog.items import Display, ReleaseItem, SkillBundleDescriptor
from harness_core.skills import skill_bundle_manifest

if TYPE_CHECKING:
    from app.agent_loop_lib.modules.providers.skills.base import Skill

BUILTIN_PACKS_ROOT = Path(__file__).with_name("builtin_packs")


class SkillPacksCatalogSource:
    kind = "skill"
    catalog_file = "skills.json"

    def __init__(self, seeder: BuiltinSkillSeeder) -> None:
        self._seeder = seeder

    def items(self) -> list[ReleaseItem]:
        return [self._item(skill) for skill in self._seeder.packs]

    def _item(self, skill: Skill) -> ReleaseItem:
        meta = skill.metadata
        files = {path: text.encode("utf-8") for path, text in self._seeder.seeded_files(skill).items()}
        return ReleaseItem(
            kind=self.kind,
            name=meta.name,
            version=meta.pack_version or meta.version,
            provenance="builtin",
            display=Display(
                name=meta.name,
                description=meta.description,
                categories=tuple(c for c in (meta.category, meta.subcategory) if c),
            ),
            descriptor_kind="skill-bundle",
            descriptors=(SkillBundleDescriptor(meta.name, skill_bundle_manifest(files)),),
        )


def release_source() -> SkillPacksCatalogSource:
    return SkillPacksCatalogSource(BuiltinSkillSeeder(str(BUILTIN_PACKS_ROOT)))

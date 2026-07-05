from __future__ import annotations

import logging
import os
import shutil

import yaml

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillFilter,
    SkillMetadata,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.loader import (
    iter_skill_dirs,
    load_skill_file,
    parse_skill_md,
)
from app.agent_loop_lib.modules.providers.skills.store import SkillStore
from app.agent_loop_lib.modules.providers.skills.validator import (
    SkillFormatError,
    SkillValidator,
)

"""Filesystem `SkillStore` — multi-root with exactly one writable primary
(mirrors Hermes's `external_dirs` pattern):

- **Primary root** (`skills_dir`): read-write; every create/update/delete
  lands here.
- **Extra roots** (`extra_skills_dirs`): read-only scans — how externally
  installed skills work today and tomorrow: npm/npx skill packs (e.g.
  `npx skills add` installing into `.agents/skills/`), shared team
  directories, checked-out skill monorepos. A non-existent path is silently
  skipped (`iter_skill_dirs` already no-ops on a missing root).
- **Precedence**: the primary root shadows extra roots on name collision.
- **Directory hygiene**: `_`/`.`-prefixed entries are never scanned as
  skills (protects `_meta/`, `.git/`, etc. — enforced by `iter_skill_dirs`).

An in-memory `name -> skill_dir` location cache is built at construction
and kept incrementally up to date by every write method; call `refresh()`
to pick up out-of-process changes (a human editing a SKILL.md by hand, a UI
markdown editor, a fresh package install into an extra root).
"""

logger = logging.getLogger(__name__)


def _safe_join(base: str, rel: str) -> str | None:
    """Resolve `rel` under `base`, rejecting any path that would escape it
    (e.g. '../../etc/passwd') — returns None on an escape attempt."""
    base_abs = os.path.abspath(base)
    full = os.path.abspath(os.path.join(base_abs, rel))
    if os.path.commonpath([full, base_abs]) != base_abs:
        return None
    return full


def _matches(metadata: SkillMetadata, filt: SkillFilter) -> bool:
    if filt.category is not None and metadata.category != filt.category:
        return False
    if filt.subcategory is not None and metadata.subcategory != filt.subcategory:
        return False
    if filt.status is not None and metadata.status != filt.status:
        return False
    if filt.source is not None and metadata.source != filt.source:
        return False
    if filt.tags and not (set(filt.tags) & set(metadata.tags)):
        return False
    if filt.query:
        haystack = f"{metadata.name} {metadata.description} {' '.join(metadata.tags)}".lower()
        if filt.query.lower() not in haystack:
            return False
    return True


class FilesystemSkillStore(SkillStore):
    def __init__(
        self,
        skills_dir: str,
        extra_skills_dirs: list[str] | None = None,
        validator: SkillValidator | None = None,
    ) -> None:
        self._primary = os.path.abspath(skills_dir)
        self._extra_roots = [os.path.abspath(p) for p in (extra_skills_dirs or [])]
        self._validator = validator or SkillValidator()
        self._locations: dict[str, str] = {}
        # Directory-inferred (category, subcategory) per skill name — a
        # skill's category lives in *where* it sits on disk, not necessarily
        # in its own frontmatter (create_skill doesn't rewrite the content
        # it's handed just to embed the category the caller placed it
        # under), so `_load` needs this to pass the same hint `loader.
        # load_skills_from_dir` would have inferred via `iter_skill_dirs`.
        self._categories: dict[str, tuple[str | None, str | None]] = {}
        os.makedirs(self._primary, exist_ok=True)
        self.refresh()

    def refresh(self) -> None:
        """Full rescan of every root. Extra roots are scanned first (lower
        precedence), then the primary root — so a primary-root skill always
        shadows a same-named skill in an extra root."""
        locations: dict[str, str] = {}
        categories: dict[str, tuple[str | None, str | None]] = {}
        for root in self._extra_roots:
            for skill_dir, category, subcategory in iter_skill_dirs(root):
                name = os.path.basename(skill_dir)
                locations[name] = skill_dir
                categories[name] = (category, subcategory)
        for skill_dir, category, subcategory in iter_skill_dirs(self._primary):
            name = os.path.basename(skill_dir)
            locations[name] = skill_dir
            categories[name] = (category, subcategory)
        self._locations = locations
        self._categories = categories

    def _is_writable(self, skill_dir: str) -> bool:
        return os.path.commonpath([skill_dir, self._primary]) == self._primary

    def _load(self, skill_dir: str) -> Skill | None:
        path = os.path.join(skill_dir, "SKILL.md")
        category, subcategory = self._categories.get(os.path.basename(skill_dir), (None, None))
        try:
            return load_skill_file(path, category=category, subcategory=subcategory, validator=self._validator)
        except (SkillFormatError, OSError) as e:
            logger.warning("Skipping unreadable skill at %s: %s", skill_dir, e)
            return None

    def _write_raw(self, skill_dir: str, content: str) -> None:
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(content)

    def _persist(self, skill_dir: str, skill: Skill) -> None:
        frontmatter_yaml = yaml.safe_dump(skill.metadata.to_frontmatter_dict(), sort_keys=False)
        content = f"---\n{frontmatter_yaml}---\n\n{skill.body.strip()}\n"
        self._write_raw(skill_dir, content)

    def _dir_for(self, name: str, category: str | None, subcategory: str | None) -> str:
        parts = [self._primary]
        if category:
            parts.append(category)
            if subcategory:
                parts.append(subcategory)
        parts.append(name)
        return os.path.join(*parts)

    # ---- SkillReader --------------------------------------------------

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:
        results: list[SkillMetadata] = []
        for name in sorted(self._locations):
            skill = self._load(self._locations[name])
            if skill is None:
                continue
            if filter is not None and not _matches(skill.metadata, filter):
                continue
            results.append(skill.metadata)
        return results

    async def get_skill(self, name: str) -> Skill | None:
        skill_dir = self._locations.get(name)
        return self._load(skill_dir) if skill_dir is not None else None

    async def get_resource(self, skill_name: str, resource_path: str) -> str | None:
        skill_dir = self._locations.get(skill_name)
        if skill_dir is None:
            return None
        full = _safe_join(skill_dir, resource_path)
        if full is None or not os.path.isfile(full):
            return None
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    async def exists(self, name: str) -> bool:
        return name in self._locations

    # ---- SkillWriter ----------------------------------------------------

    async def create_skill(
        self, name: str, content: str, category: str | None = None, subcategory: str | None = None,
    ) -> SkillMetadata:
        if name in self._locations:
            raise RegistryError(f"Skill {name!r} already exists")
        skill = parse_skill_md(content, expected_name=name, validator=self._validator)
        self._validator.validate_skill(skill, expected_name=name)
        skill_dir = self._dir_for(name, category, subcategory)
        os.makedirs(skill_dir, exist_ok=True)
        self._write_raw(skill_dir, content)
        self._locations[name] = skill_dir
        self._categories[name] = (category, subcategory)

        # Mirror loader.load_skill_file's precedence: an explicit frontmatter
        # category/subcategory wins; the directory placement is only a
        # default for the metadata returned to the caller (e.g. SkillManager
        # updates its catalog/index from this return value immediately,
        # without waiting on a `refresh()`).
        meta_updates: dict[str, str] = {}
        if category is not None and skill.metadata.category is None:
            meta_updates["category"] = category
        if subcategory is not None and skill.metadata.subcategory is None:
            meta_updates["subcategory"] = subcategory
        if meta_updates:
            skill = skill.model_copy(update={"metadata": skill.metadata.model_copy(update=meta_updates)})
        return skill.metadata

    async def update_skill(self, name: str, content: str) -> SkillMetadata:
        skill_dir = self._locations.get(name)
        if skill_dir is None:
            raise RegistryError(f"Skill {name!r} not found")
        if not self._is_writable(skill_dir):
            raise SkillFormatError(f"Skill {name!r} lives in a read-only root and cannot be modified")
        skill = parse_skill_md(content, expected_name=name, validator=self._validator)
        self._validator.validate_skill(skill, expected_name=name)
        self._write_raw(skill_dir, content)
        return skill.metadata

    async def patch_skill(self, name: str, old_string: str, new_string: str) -> bool:
        skill_dir = self._locations.get(name)
        if skill_dir is None or not self._is_writable(skill_dir):
            return False
        skill = self._load(skill_dir)
        if skill is None or skill.body.count(old_string) != 1:
            return False
        new_body = skill.body.replace(old_string, new_string, 1)
        self._validator.validate_body(new_body)
        self._persist(skill_dir, skill.model_copy(update={"body": new_body}))
        return True

    async def delete_skill(self, name: str) -> bool:
        skill_dir = self._locations.get(name)
        if skill_dir is None or not self._is_writable(skill_dir):
            return False
        shutil.rmtree(skill_dir, ignore_errors=True)
        del self._locations[name]
        self._categories.pop(name, None)
        return True

    async def write_resource(self, skill_name: str, path: str, content: str) -> bool:
        skill_dir = self._locations.get(skill_name)
        if skill_dir is None or not self._is_writable(skill_dir):
            return False
        full = _safe_join(skill_dir, path)
        if full is None:
            return False
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return True

    async def remove_resource(self, skill_name: str, path: str) -> bool:
        skill_dir = self._locations.get(skill_name)
        if skill_dir is None or not self._is_writable(skill_dir):
            return False
        full = _safe_join(skill_dir, path)
        if full is None or not os.path.isfile(full):
            return False
        os.remove(full)
        return True

    async def deprecate_skill(self, name: str, reason: str, replaced_by: str | None = None) -> bool:
        skill_dir = self._locations.get(name)
        if skill_dir is None or not self._is_writable(skill_dir):
            return False
        skill = self._load(skill_dir)
        if skill is None:
            return False
        updated_metadata = skill.metadata.model_copy(update={
            "status": SkillStatus.DEPRECATED,
            "deprecated_reason": reason,
            "replaced_by": replaced_by,
        })
        self._persist(skill_dir, skill.model_copy(update={"metadata": updated_metadata}))
        return True

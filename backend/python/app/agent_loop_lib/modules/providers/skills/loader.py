from __future__ import annotations

import logging
import os
import re

import yaml

from app.agent_loop_lib.modules.providers.skills.base import Skill
from app.agent_loop_lib.modules.providers.skills.validator import (
    SkillFormatError,
    SkillValidator,
)

"""Parses SKILL.md files per the agentskills.io spec
(https://agentskills.io/specification) — the same format used by
github.com/anthropics/skills and github.com/openai/skills, so skills authored
for those ecosystems load here unmodified. Field validation itself lives in
`validator.py` (the single source of truth shared with the write path);
this module owns parsing/discovery: YAML frontmatter splitting, recursive
category-aware directory scanning, and bundled-resource listing.

Re-exports `SkillFormatError` from `validator.py` for backward compatibility
with existing call sites that import it from here.
"""

__all__ = [
    "SkillFormatError",
    "parse_skill_md",
    "discover_resources",
    "iter_skill_dirs",
    "load_skill_file",
    "load_skills_from_dir",
]

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*\n?(.*)\Z", re.DOTALL)
_RESOURCE_KINDS = ("scripts", "references", "assets")
_IGNORED_PREFIXES = ("_", ".")

_default_validator = SkillValidator()


def parse_skill_md(content: str, *, expected_name: str | None = None, validator: SkillValidator | None = None) -> Skill:
    """Parse raw SKILL.md text (YAML frontmatter + Markdown body) into a
    `Skill`. `expected_name` enforces the spec's "name must match the parent
    directory name" rule when loading from a `<skill-name>/SKILL.md` layout;
    pass None to skip that check (e.g. parsing a standalone string in tests).
    """
    from app.agent_loop_lib.modules.providers.skills.base import SkillMetadata

    validator = validator or _default_validator
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise SkillFormatError("SKILL.md must start with YAML frontmatter delimited by '---' lines")
    raw_frontmatter, body = match.groups()

    try:
        data = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as e:
        raise SkillFormatError(f"Invalid YAML frontmatter: {e}") from e
    if not isinstance(data, dict):
        raise SkillFormatError("YAML frontmatter must be a mapping of key: value pairs")

    validator.validate_frontmatter(data)
    metadata = SkillMetadata.from_raw(data)
    if expected_name is not None and metadata.name != expected_name:
        raise SkillFormatError(
            f"Skill 'name' ({metadata.name!r}) must match its directory name ({expected_name!r})"
        )
    return Skill(metadata=metadata, body=body.strip("\n"))


def discover_resources(skill_dir: str) -> dict[str, list[str]]:
    """List bundled resource files under a skill's `scripts/`, `references/`,
    and `assets/` subdirectories (relative to `skill_dir`) — level-3
    progressive disclosure. Contents are never read here, only enumerated."""
    resources: dict[str, list[str]] = {}
    for kind in _RESOURCE_KINDS:
        kind_dir = os.path.join(skill_dir, kind)
        if not os.path.isdir(kind_dir):
            continue
        files: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(kind_dir):
            for filename in filenames:
                rel = os.path.relpath(os.path.join(dirpath, filename), skill_dir)
                files.append(rel)
        if files:
            resources[kind] = sorted(files)
    return resources


def iter_skill_dirs(root: str, max_category_depth: int = 2):
    """Yield `(skill_dir, category, subcategory)` for every SKILL.md found
    under `root`, walking up to `max_category_depth` levels of category
    directories (category/subcategory/skill-name/SKILL.md) before treating a
    directory as the skill's own directory. A directory that neither
    contains a SKILL.md nor is shallow enough to recurse into further is
    silently skipped. Entries whose name starts with '_' or '.' are always
    skipped — this is what protects manager-owned directories like `_meta/`
    and VCS directories like `.git/` from ever being scanned as skills.
    """
    if not os.path.isdir(root):
        return
    yield from _walk_for_skills(root, depth=0, max_depth=max_category_depth, category=None, subcategory=None)


def _walk_for_skills(current_dir: str, depth: int, max_depth: int, category: str | None, subcategory: str | None):
    for entry in sorted(os.listdir(current_dir)):
        if entry.startswith(_IGNORED_PREFIXES):
            continue
        entry_path = os.path.join(current_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        if os.path.isfile(os.path.join(entry_path, "SKILL.md")):
            yield entry_path, category, subcategory
        elif depth < max_depth:
            next_category = category if category is not None else entry
            next_subcategory = entry if category is not None else None
            yield from _walk_for_skills(entry_path, depth + 1, max_depth, next_category, next_subcategory)
        # else: too deep to still be a category dir, and not a skill dir
        # itself — not a valid layout, skip rather than raise so one odd
        # directory can't take down a whole scan.


def load_skill_file(
    path: str,
    *,
    category: str | None = None,
    subcategory: str | None = None,
    validator: SkillValidator | None = None,
) -> Skill:
    """Load one SKILL.md file. When it lives at `<skill-name>/SKILL.md` (the
    standard layout), the directory name becomes both the enforced expected
    `name` and the `root_dir` recorded on the Skill for resolving its
    bundled resources. `category`/`subcategory`, when given (from
    `iter_skill_dirs`'s directory-inferred grouping), are applied as
    defaults ONLY when the SKILL.md's own frontmatter didn't already declare
    them — an explicit frontmatter value always wins."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    root_dir = os.path.dirname(os.path.abspath(path))
    expected_name = os.path.basename(root_dir) or None
    skill = parse_skill_md(content, expected_name=expected_name, validator=validator)

    meta_updates: dict[str, str] = {}
    if category is not None and skill.metadata.category is None:
        meta_updates["category"] = category
    if subcategory is not None and skill.metadata.subcategory is None:
        meta_updates["subcategory"] = subcategory
    if meta_updates:
        skill = skill.model_copy(update={"metadata": skill.metadata.model_copy(update=meta_updates)})

    return skill.model_copy(update={"root_dir": root_dir, "resources": discover_resources(root_dir)})


def load_skills_from_dir(root: str, *, max_category_depth: int = 2) -> list[Skill]:
    """Recursively scan `root` (up to `max_category_depth` levels of
    category directories — see `iter_skill_dirs`) and load every valid
    skill found. A malformed or unreadable skill is logged and skipped
    rather than raising, so one broken third-party skill can't take down
    the whole manager."""
    skills: list[Skill] = []
    for skill_dir, category, subcategory in iter_skill_dirs(root, max_category_depth):
        skill_path = os.path.join(skill_dir, "SKILL.md")
        try:
            skills.append(load_skill_file(skill_path, category=category, subcategory=subcategory))
        except (SkillFormatError, OSError) as e:
            logger.warning("Skipping invalid skill at %s: %s", skill_path, e)
    return skills

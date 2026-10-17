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
    "render_skill_md",
    "discover_resources",
    "read_resources",
    "iter_skill_dirs",
    "load_skill_file",
    "load_skills_from_dir",
]

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*\n?(.*)\Z", re.DOTALL)
_IGNORED_PREFIXES = ("_", ".")
# Directories never scanned as bundled resources, on top of any dotfile/
# dot-directory (see `_should_ignore_resource`) — build artifacts and VCS
# metadata that could otherwise be picked up from an unclean local checkout
# or a source archive.
_IGNORED_RESOURCE_DIR_NAMES = frozenset({"__pycache__", "node_modules", ".git"})

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


def render_skill_md(skill: Skill) -> str:
    """Inverse of `parse_skill_md`: serialize a `Skill` back into a full
    SKILL.md string (YAML frontmatter + body) — the one place this
    round-trip happens, shared by every store that needs to re-persist a
    `Skill` it has already parsed and mutated in memory (e.g. after a
    `patch`/`deprecate`/`rollback`), rather than each store re-implementing
    the same two-line YAML dump."""
    frontmatter_yaml = yaml.safe_dump(skill.metadata.to_frontmatter_dict(), sort_keys=False)
    return f"---\n{frontmatter_yaml}---\n\n{skill.body.strip()}\n"


def _should_ignore_resource(rel_path: str) -> bool:
    """`rel_path` is already POSIX-separated (see `discover_resources`)."""
    if rel_path == "SKILL.md" or rel_path.endswith(".pyc"):
        return True
    return any(part.startswith(".") for part in rel_path.split("/"))


def discover_resources(skill_dir: str) -> dict[str, list[str]]:
    """List every bundled resource file under a skill's directory (relative
    to `skill_dir`) — level-3 progressive disclosure. The agentskills.io spec
    allows "any additional files or directories" a skill needs (real
    community skills rely on this: Anthropic's `pdf` skill has `forms.md`/
    `reference.md` at the skill root, `docx` has an `ooxml/` directory of
    scripts and schemas alongside the conventional `scripts/`/`references/`/
    `assets/`), so nothing here is restricted to those three names — only
    `SKILL.md` itself, dotfiles/dot-directories, `__pycache__/`,
    `node_modules/`, `.git/`, and `*.pyc` are excluded. Root-level files
    (no subdirectory) are grouped under the synthetic `"files"` kind so
    `Skill.resources` stays the same `dict[str, list[str]]` shape every
    caller (REST, `load_skill`, the frontend) already expects. Contents are
    never read here, only enumerated."""
    resources: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _IGNORED_RESOURCE_DIR_NAMES
        ]
        for filename in filenames:
            rel = os.path.relpath(os.path.join(dirpath, filename), skill_dir).replace(os.sep, "/")
            if _should_ignore_resource(rel):
                continue
            kind = rel.split("/", 1)[0] if "/" in rel else "files"
            resources.setdefault(kind, []).append(rel)
    return {kind: sorted(paths) for kind, paths in resources.items()}


def read_resources(skill_dir: str, listing: dict[str, list[str]]) -> dict[str, str]:
    """Read every resource path enumerated by `listing` (as returned by
    `discover_resources`) into a `{relative_path: text}` map. Used by
    `BuiltinSkillSeeder` to bundle an in-repo pack's scripts/references/
    assets into the graph doc it creates/upgrades, so a builtin's SKILL.md
    instructions that invoke a bundled script (e.g. `python skills/
    office-utils/scripts/unpack.py`) actually have that script available in
    the sandbox. Binary files (non-UTF-8) are skipped with a warning logged
    — same lenient behavior as `package_importer.py`'s text-only import."""
    contents: dict[str, str] = {}
    for paths in listing.values():
        for rel in paths:
            full = os.path.join(skill_dir, *rel.split("/"))
            try:
                with open(full, "rb") as f:
                    data = f.read()
                contents[rel] = data.decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("Skipping unreadable/binary resource %s: %s", full, e)
    return contents


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

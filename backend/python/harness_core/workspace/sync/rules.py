"""Pure rules of the sync: what an engine must never read as configuration, what a sandbox may hand back.

Every function here is table-tested (``tests/unit/harness_core/workspace/sync/test_rules.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.workspace.safe_names import (
    UnsafeName,
    check_portable_path,
    link_target_escapes,
)
from harness_core.workspace.sync.types import (
    ENGINE_CONFIG,
    SHADOWED,
    SYMLINK_ESCAPES,
    UNSTORABLE_PATH,
    UnsafeArchiveMember,
)
from harness_core.workspace.types import normalize_workspace_file_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# An engine loads these from its working tree as plug-ins, hooks, settings,
# commands or MCP servers. Engines get configuration only from render_launch,
# so a workspace file must never be one of them (ARCHITECTURE §4.3).
ENGINE_CONFIG_DIRS = frozenset({".opencode", ".claude", ".codex"})
ENGINE_CONFIG_FILES = frozenset({"opencode.json", "opencode.jsonc", ".mcp.json"})
MAX_LINK_TARGET_BYTES = 4096
# ``ph git clone`` (WP-63) sets ``pipeshub.fetched = true`` in the checkout's git config; a config bigger than this
# is no checkout ph made and is never read.
GIT_CONFIG = ".git/config"
MAX_GIT_CONFIG_BYTES = 64 * 1024


def is_engine_config(path: str) -> bool:
    """Any segment is a plug-in folder, or the file is an engine's project config, at any depth."""
    parts = path.split("/")
    return parts[-1] in ENGINE_CONFIG_FILES or any(part in ENGINE_CONFIG_DIRS for part in parts)


def engine_config_root(path: str) -> str:
    """The shortest prefix of ``path`` that is engine configuration: the folder to remove whole."""
    parts = path.split("/")
    for index, part in enumerate(parts):
        if part in ENGINE_CONFIG_DIRS:
            return "/".join(parts[: index + 1])
    return path


def fetched_checkout_of(config_path: str) -> str | None:
    """The folder whose git config ``config_path`` is (``repos/onyx/.git/config`` → ``repos/onyx``), or ``None``.

    Never the workspace root: a repository there is the run's own (GIT-FLOW), and ``ph git clone`` only clones into
    an empty folder."""
    if not config_path.endswith("/" + GIT_CONFIG):
        return None
    folder = config_path[: -len(GIT_CONFIG) - 1]
    return None if is_engine_config(folder) else folder


def marks_fetched(config: bytes) -> bool:
    """Whether a git config has ``fetched = true`` in its ``[pipeshub]`` section, as ``ph git clone`` writes it."""
    section = ""
    for raw in config.decode("utf-8", "replace").splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if line.startswith("[") and "]" in line:
            section = line[1 : line.index("]")].strip().split(" ", 1)[0].lower()
            continue
        key, equals, value = line.partition("=")
        if section == "pipeshub" and equals and key.strip().lower() == "fetched" and value.strip().lower() == "true":
            return True
    return False


def under_any(path: str, folders: Iterable[str]) -> bool:
    """Whether ``path`` is inside one of ``folders`` (or is one)."""
    return any(path == folder or path.startswith(folder + "/") for folder in folders)


def link_escapes(path: str, target: str, links: Iterable[str] = ()) -> bool:
    """Whether a link at ``path`` may point outside the root.

    The target is resolved lexically from the link's folder; passing through
    another link counts as escaping, because the kernel would resolve that one
    first and lexical ``..`` no longer describes where it ends up.
    """
    # SEC-FS-02: a target with a backslash, a drive or an encoded separator is a path on some other system.
    if link_target_escapes(target):
        return True
    if len(target.encode("utf-8", "surrogateescape")) > MAX_LINK_TARGET_BYTES:
        return True
    others = frozenset(links) - {path}
    resolved = path.split("/")[:-1]
    for segment in target.split("/"):
        if segment in ("", "."):
            continue
        if resolved and "/".join(resolved) in others:
            return True
        if segment == "..":
            if not resolved:
                return True
            _ = resolved.pop()
        else:
            resolved.append(segment)
    return False


@dataclass(frozen=True, slots=True)
class TreePlan:
    """Which manifest paths a sandbox gets; the rest, with the reason, stay in the store only."""

    visible: frozenset[str]
    hidden: Mapping[str, str]


def plan_tree(paths: Iterable[str], links: Mapping[str, str]) -> TreePlan:
    """Split a manifest's paths into what is written to a sandbox and what is kept out.

    ``links`` maps each symbolic link's path to its target. A path below a
    visible file or link is ``shadowed``: writing it would mean writing through
    a link, or replacing a file with a folder the manifest also calls a file.
    """
    hidden: dict[str, str] = {}
    candidates: list[str] = []
    for path in sorted(paths):
        if is_engine_config(path):
            hidden[path] = ENGINE_CONFIG
        elif path in links and link_escapes(path, links[path], links):
            hidden[path] = SYMLINK_ESCAPES
        else:
            candidates.append(path)
    leaves = frozenset(candidates)
    visible: set[str] = set()
    for path in candidates:
        parts = path.split("/")
        if any("/".join(parts[:i]) in leaves for i in range(1, len(parts))):
            hidden[path] = SHADOWED
        else:
            visible.add(path)
    return TreePlan(frozenset(visible), hidden)


@dataclass(frozen=True, slots=True)
class MemberPath:
    """An archive member's place under the root: ``path`` (canonical) or why it cannot be stored.

    ``raw`` is the name relative to the root as the sandbox has it, which is
    what removing it from the sandbox needs. ``raw`` is ``""`` for the root.
    """

    raw: str
    path: str | None
    skip: str | None = None


def member_path(name: str, base: str) -> MemberPath:
    """Place a member named ``name`` in an archive of the folder ``base`` (``get_tar`` prefixes names with it).

    Raises ``UnsafeArchiveMember`` for a name a correct broker never produces;
    returns a skip reason for a name the workspace store cannot hold.
    """
    name = name.rstrip("/")
    if "\x00" in name:
        raise UnsafeArchiveMember("archive member name has a NUL")
    if name == base:
        return MemberPath("", None)
    if not name.startswith(base + "/"):
        raise UnsafeArchiveMember("archive member is outside the folder that was asked for")
    raw = name[len(base) + 1 :]
    if any(segment in ("", ".", "..") for segment in raw.split("/")):
        raise UnsafeArchiveMember("archive member name is not canonical or traverses")
    try:
        canonical = normalize_workspace_file_path(raw)
    except ValueError:
        return MemberPath(raw, None, UNSTORABLE_PATH)
    if canonical != raw:
        return MemberPath(raw, None, UNSTORABLE_PATH)
    # SEC-FS-02: a drive or an encoded separator in a name is kept out of the store, and so out of every archive.
    try:
        _ = check_portable_path(canonical)
    except UnsafeName:
        return MemberPath(raw, None, UNSTORABLE_PATH)
    return MemberPath(raw, canonical)


__all__ = [
    "ENGINE_CONFIG_DIRS",
    "ENGINE_CONFIG_FILES",
    "GIT_CONFIG",
    "MAX_GIT_CONFIG_BYTES",
    "MAX_LINK_TARGET_BYTES",
    "MemberPath",
    "TreePlan",
    "engine_config_root",
    "fetched_checkout_of",
    "is_engine_config",
    "link_escapes",
    "marks_fetched",
    "member_path",
    "plan_tree",
    "under_any",
]

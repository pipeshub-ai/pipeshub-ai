"""tsconfig.json / jsconfig.json `baseUrl` and `paths`, so `@/lib/http` resolves the way the compiler does.

A config's settings come from its stored JSON parse (``RecordFacts.structured_fields``), merged with the
configs it ``extends``. The tsconfig.json (else jsconfig.json) nearest above a file applies to it. One
that could not be read holds its folder with no settings, so its files keep the fallback rules.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["TsConfigs", "is_ts_config"]

# A folder's own config; the first present wins, as the compiler prefers tsconfig.json.
_FOLDER_CONFIGS = ("tsconfig.json", "jsconfig.json")
_BASE_URL = "compilerOptions.baseUrl"
_PATHS = "compilerOptions.paths."
_EXTENDS = "extends"
_MAX_EXTENDS = 8


def is_ts_config(path: str) -> bool:
    """A tsconfig*/jsconfig*.json: a folder's config, or one a config extends.

    shortcut: an `extends` target named otherwise (`configs/base.json`) is never read; widen this test
    when a repo needs it.
    """
    name = posixpath.basename(path)
    return name.endswith(".json") and name.startswith(("tsconfig", "jsconfig"))


@dataclass(frozen=True)
class _Settings:
    base_url: str | None = None
    # pattern -> repo-relative targets, each `*` still to be substituted
    paths: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class _Merged:
    base_url: str | None = None
    paths: dict[str, list[str]] | None = None
    # Where `paths` targets are relative to when no baseUrl is set: the folder of the config defining them.
    paths_folder: str = ""
    # Every config path read (or looked for) on the way, so an edit to any of them is seen.
    chain: set[str] = field(default_factory=set)


def _join(folder: str, relative: str) -> str | None:
    """*relative* against *folder*, repo-relative; None once it leaves the repo."""
    joined = posixpath.normpath(posixpath.join(folder, relative.strip()))
    if joined == ".":
        return ""
    return None if joined == ".." or joined.startswith(("../", "/")) else joined


class TsConfigs:
    """The repo's configs, keyed by path; *fields* is None for one whose parse could not be read."""

    def __init__(self, fields_by_path: Mapping[str, Mapping[str, str] | None]) -> None:
        self.unread = sorted(path for path, fields in fields_by_path.items() if fields is None)
        self._fields = {path: fields or {} for path, fields in fields_by_path.items()}
        self._by_folder: dict[str, _Settings] = {}
        self._chains: dict[str, set[str]] = {}
        for folder in {posixpath.dirname(path) for path in self._fields}:
            path = next((p for name in _FOLDER_CONFIGS
                         if (p := posixpath.join(folder, name)) in self._fields), None)
            if path is None:
                continue
            merged = self._merge(path, 0)
            root = merged.base_url if merged.base_url is not None else merged.paths_folder
            self._by_folder[folder] = _Settings(merged.base_url, {
                pattern: tuple(t for target in targets if (t := _join(root, target)) is not None)
                for pattern, targets in (merged.paths or {}).items()
            })
            self._chains[folder] = merged.chain

    def _merge(self, path: str, depth: int) -> _Merged:
        """*path*'s settings over those of what it extends, later `extends` entries winning."""
        fields = self._fields.get(path, {})
        folder = posixpath.dirname(path)
        out = _Merged(chain={path})
        if depth < _MAX_EXTENDS:
            for parent in self._extends(fields.get(_EXTENDS, ""), folder):
                inherited = self._merge(parent, depth + 1)
                out.chain |= inherited.chain
                if inherited.base_url is not None:
                    out.base_url = inherited.base_url
                if inherited.paths is not None:
                    out.paths, out.paths_folder = inherited.paths, inherited.paths_folder
        if _BASE_URL in fields:
            out.base_url = _join(folder, fields[_BASE_URL])
        own = {key[len(_PATHS):]: [t for t in value.split(", ") if t.strip()]
               for key, value in fields.items() if key.startswith(_PATHS)}
        # `"paths": {}` is stored as the text "{}" and clears what the config extends, as in the compiler.
        if own or fields.get(_PATHS[:-1]) == "{}":
            out.paths, out.paths_folder = own, folder
        return out

    def _extends(self, value: str, folder: str) -> list[str]:
        """The repo paths an `extends` value (one path, or a list as "a, b") names.

        shortcut: a package (`@tsconfig/node16/tsconfig.json`) is not followed; it is not in the repo.
        """
        out = []
        for spec in (s.strip() for s in value.split(", ")):
            path = _join(folder, spec) if spec.startswith(("./", "../")) else None
            if path:
                out.append(path if path in self._fields or path.endswith(".json") else f"{path}.json")
        return out

    def candidates(self, specifier: str, from_file: str) -> list[str]:
        """Repo paths a non-relative *specifier* may name under the config nearest *from_file*, in the
        compiler's order: the `paths` pattern matching it (exact, else the longest prefix), then baseUrl."""
        settings = self._nearest(from_file)
        if settings is None:
            return []
        out = []
        match = _match(settings.paths, specifier)
        if match is not None:
            pattern, star = match
            out.extend(target.replace("*", star, 1) for target in settings.paths[pattern])
        if settings.base_url is not None and (path := _join(settings.base_url, specifier)):
            out.append(path)
        return out

    def _nearest(self, file_path: str) -> _Settings | None:
        folder = posixpath.dirname(file_path)
        while True:
            if folder in self._by_folder:
                return self._by_folder[folder]
            if not folder:
                return None
            folder = posixpath.dirname(folder)

    def folders_reading(self, paths: Iterable[str]) -> set[str]:
        """Folders whose settings *paths* (configs added, edited or deleted) may have changed."""
        paths = set(paths)
        return {posixpath.dirname(p) for p in paths if posixpath.basename(p) in _FOLDER_CONFIGS} \
            | {folder for folder, chain in self._chains.items() if chain & paths}


def _match(paths: Mapping[str, tuple[str, ...]], specifier: str) -> tuple[str, str] | None:
    """(pattern, what its `*` matched) for *specifier*: an exact pattern, else the longest prefix."""
    if specifier in paths:
        return specifier, ""
    best: tuple[str, str] | None = None
    longest = -1
    for pattern in paths:
        prefix, star, suffix = pattern.partition("*")
        if star and len(prefix) > longest and len(specifier) >= len(prefix) + len(suffix) \
                and specifier.startswith(prefix) and specifier.endswith(suffix):
            best, longest = (pattern, specifier[len(prefix):len(specifier) - len(suffix)]), len(prefix)
    return best

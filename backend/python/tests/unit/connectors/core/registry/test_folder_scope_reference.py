"""Folder patterns decide as an independent reference matcher does.

The reference walks the pattern recursively (a ``**`` segment either ends or
eats one more folder) and matches each folder name with
``fnmatch.fnmatchcase``, after the store's normalisation. It shares no code
with ``FolderScope``.
"""

from __future__ import annotations

import fnmatch
import unicodedata
from functools import cache

from hypothesis import given, settings
from hypothesis import strategies as st

from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
)
from app.connectors.core.registry.folder_scope import (
    FILE_SHARE_PATHS,
    OBJECT_STORE_PATHS,
    FolderRole,
    FolderScope,
)

NAME_PIECES = ("a", "A", "b", " ", "é", "é", ".", "[", "]", "!", "\\", "\n", "*", "?", "\\*", "\\?")
PATH_PIECES = ("a", "A", "b", "ab", " ", "/", "é", "é", ".", "[", "]", "!", "\\", "\n", "*", "?")
segment = st.one_of(st.just("**"), st.lists(st.sampled_from(NAME_PIECES), min_size=1, max_size=4).map("".join))
value = st.lists(segment, min_size=1, max_size=4).map("/".join)
name = st.lists(st.sampled_from(PATH_PIECES), min_size=1, max_size=3).map("".join)
path = st.lists(name, max_size=5).map("/".join)


def _n(s: str, share: bool) -> str:
    if not share:
        return s
    s = unicodedata.normalize("NFC", s.replace("\\", "/"))
    return unicodedata.normalize("NFC", s.casefold())


def _tokens(text: str, escapes: bool) -> list[tuple[str, bool]]:
    """(character, is_wildcard) pairs; ``\\*`` and ``\\?`` are plain characters when escapes are on."""
    out: list[tuple[str, bool]] = []
    i = 0
    while i < len(text):
        if escapes and text[i] == "\\" and i + 1 < len(text) and text[i + 1] in "*?":
            out.append((text[i + 1], False))
            i += 2
        else:
            out.append((text[i], text[i] in "*?"))
            i += 1
    return out


def _fnmatch_text(seg: str, escapes: bool) -> str:
    """The segment as an fnmatch pattern with no character classes."""
    return "".join(c if wild else ("[[]" if c == "[" else f"[{c}]" if c in "*?" else c)
                   for c, wild in _tokens(seg, escapes))


def _names_of_key(key: str) -> tuple[tuple[str, ...], str]:
    """Read a key one character at a time: (the names of its folders, its last name).

    Leading slashes are skipped. After that every slash closes one name, an empty
    one included, so ``a//f`` is in the folders ``a`` and the empty name.
    """
    i = 0
    while i < len(key) and key[i] == "/":
        i += 1
    names: list[str] = []
    current: list[str] = []
    for ch in key[i:]:
        if ch == "/":
            names.append("".join(current))
            current = []
        else:
            current.append(ch)
    return tuple(names), "".join(current)


def _folder_names(folder: str) -> tuple[str, ...]:
    """A folder path names its folders; a trailing slash only marks it as one."""
    names, last = _names_of_key(folder)
    if last:
        return (*names, last)
    # "a/" ends a name with its slash; an empty path or "/" names the top.
    return names


class Reference:
    def __init__(self, values: list[str], exclude: bool, share: bool) -> None:
        self.exclude, self.share = exclude, share
        escapes = not share
        self.literals: list[str] = []
        self.patterns: list[tuple[str, ...]] = []
        for v in values:
            entry = v.strip()
            if share:
                entry = entry.replace("\\", "/")
            entry = entry.strip("/")
            if any(wild for _, wild in _tokens(entry, escapes)):
                self.patterns.append(tuple(
                    "**" if s == "**" else _fnmatch_text(_n(s, share), escapes) for s in entry.split("/")
                ))
            else:
                plain = "".join(c for c, _ in _tokens(entry, escapes))
                n = _n(plain, share)
                self.literals.append(f"{n}/" if n else "")

    @staticmethod
    def _full(segs: tuple[str, ...], parts: tuple[str, ...]) -> bool:
        @cache
        def go(i: int, k: int) -> bool:
            if i == len(segs):
                return True
            if segs[i] == "**":
                return go(i + 1, k) or (k < len(parts) and go(i, k + 1))
            return k < len(parts) and fnmatch.fnmatchcase(parts[k], segs[i]) and go(i + 1, k + 1)
        return go(0, 0)

    @staticmethod
    def _open(segs: tuple[str, ...], parts: tuple[str, ...]) -> bool:
        @cache
        def go(i: int, k: int) -> bool:
            if k == len(parts):
                return i < len(segs)
            if i == len(segs):
                return False
            if segs[i] == "**":
                return go(i + 1, k) or go(i, k + 1)
            return fnmatch.fnmatchcase(parts[k], segs[i]) and go(i + 1, k + 1)
        return go(0, 0)

    def _folder(self, p: str) -> tuple[str, tuple[str, ...]]:
        # Literal folders are compared as main trims them; pattern names keep their blanks.
        n = _n(p, self.share)
        cleaned = n.strip().strip("/")
        folder = f"{cleaned}/" if cleaned else ""
        return folder, _folder_names(n)

    def role(self, p: str) -> FolderRole:
        folder, parts = self._folder(p)
        full = any(folder.startswith(f) for f in self.literals) or any(self._full(s, parts) for s in self.patterns)
        if self.exclude:
            return FolderRole.SKIP if full else FolderRole.KEEP
        if full:
            return FolderRole.KEEP
        if any(f.startswith(folder) for f in self.literals) or any(self._open(s, parts) for s in self.patterns):
            return FolderRole.PASS_THROUGH
        return FolderRole.SKIP

    def includes_file(self, p: str) -> bool:
        n = _n(p, self.share).lstrip("/")
        inside = any(n.startswith(f) for f in self.literals)
        parent, _ = _names_of_key(n)
        inside = inside or any(self._full(s, parent) for s in self.patterns)
        return not inside if self.exclude else inside


def _filters(values: list[str], exclude: bool) -> FilterCollection:
    return FilterCollection(filters=[
        Filter(key="folder_paths", value=values, type=FilterType.LIST,
               operator=ListOperator.NOT_IN if exclude else ListOperator.IN),
    ])


@settings(max_examples=3000, deadline=None)
@given(st.lists(value, min_size=1, max_size=3), st.booleans(), st.booleans(), st.lists(path, min_size=1, max_size=6))
def test_patterns_decide_as_the_reference(values: list[str], exclude: bool, share: bool, paths: list[str]) -> None:
    rules = FILE_SHARE_PATHS if share else OBJECT_STORE_PATHS
    live = FolderScope.from_filters(_filters(values, exclude), rules=rules, wildcards=True)
    ref = Reference(values, exclude, share)
    for p in paths:
        assert live.folder_role(p) is ref.role(p), p
        assert live.includes_folder(p) == (ref.role(p) is not FolderRole.SKIP), p
        assert live.includes_file(p) == ref.includes_file(p), p
        assert live.includes_file(p + "/f") == ref.includes_file(p + "/f"), p


@settings(max_examples=1000, deadline=None)
@given(st.lists(value, min_size=1, max_size=3), st.booleans(), st.lists(path, min_size=1, max_size=6))
def test_a_tracked_pattern_matched_a_kept_folder(values: list[str], share: bool, paths: list[str]) -> None:
    rules = FILE_SHARE_PATHS if share else OBJECT_STORE_PATHS
    live = FolderScope.from_filters(_filters(values, False), rules=rules, wildcards=True)
    tracker = live.tracker()
    for p in paths:
        tracker.note_folder(p, live.folder_role(p))
    kept = [p for p in paths if live.folder_role(p) is FolderRole.KEEP]
    for pattern in live.patterns:
        alone = FolderScope(patterns=(pattern,), rules=rules)
        hit = any(alone.folder_role(p) is FolderRole.KEEP for p in kept)
        assert (pattern.raw in tracker.matched()) == hit, pattern.raw


@settings(max_examples=1000, deadline=None)
@given(st.lists(value, min_size=1, max_size=4), st.booleans())
def test_no_listing_prefix_lies_inside_another(values: list[str], share: bool) -> None:
    rules = FILE_SHARE_PATHS if share else OBJECT_STORE_PATHS
    live = FolderScope.from_filters(_filters(values, False), rules=rules, wildcards=True)
    prefixes = [_n(p, share) for p in live.list_prefixes]
    for i, a in enumerate(prefixes):
        for j, b in enumerate(prefixes):
            assert i == j or not b.startswith(a), (a, b)
    # Every folder a value can keep lies under some listed prefix.
    for v in values:
        entry = v.strip().replace("\\", "/") if share else v.strip()
        first = entry.strip("/").split("/")[0]
        if first and "*" not in first and "?" not in first and "\\" not in first:
            assert any(_n(first + "/", share).startswith(p) or p.startswith(_n(first + "/", share))
                       for p in prefixes), v


@settings(max_examples=2000, deadline=None)
@given(st.lists(value, min_size=1, max_size=3), st.booleans(), st.booleans(), st.lists(path, min_size=1, max_size=6))
def test_a_folder_and_the_files_in_it_agree(values: list[str], exclude: bool, share: bool, paths: list[str]) -> None:
    """With patterns only, a file is synced exactly when its folder is kept, blanks in names included."""
    rules = FILE_SHARE_PATHS if share else OBJECT_STORE_PATHS
    live = FolderScope.from_filters(_filters(values, exclude), rules=rules, wildcards=True)
    if live.folders:
        return
    for raw in paths:
        # On a share a backslash is a slash; the file below is written with the slashes the store uses.
        p = raw.replace("\\", "/") if share else raw
        for folder in (p, p + "/", p + "//"):
            kept = (live.folder_role(folder) is not FolderRole.SKIP if exclude
                    else live.folder_role(folder) is FolderRole.KEEP)
            # A file directly in the folder, whatever empty names are right above it.
            file = folder + "f.txt" if folder.endswith("/") or not folder else folder + "/f.txt"
            assert live.includes_file(file) == kept, (folder, file)

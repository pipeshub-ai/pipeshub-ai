"""Which folders of a bucket, container or share a sync covers.

Built from the ``folder_paths`` sync filter. Paths are relative to the bucket,
container or share, use ``/`` and name folders, not arbitrary key prefixes:
``reports`` covers ``reports/2026/q1.pdf`` but not ``reports-old/a.pdf``.

A connector that reads wildcards (``from_filters(..., wildcards=True)``) also
takes folder patterns, anchored at the top: ``*`` matches within one folder
name, ``?`` one character, and a whole ``**`` segment any number of folders.
``customer/*/quotes`` covers each customer's ``quotes`` folder and all under it.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    ListOperator,
    SyncFilterKey,
    name_passes_filter,
)
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.utils.path_glob import name_regex

if TYPE_CHECKING:
    import logging
    import re
    from collections.abc import AsyncIterator, Callable, Container

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.interfaces.sync_point.isync_point import ISyncPoint
    from app.models.entities import Record

_PAGE_SIZE = 500


def _as_folder(path: str) -> str:
    """``'/a/b'`` -> ``'a/b/'``; ``''`` or ``'/'`` -> ``''`` (the whole store)."""
    cleaned = path.strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def path_in_container(container_name: str | None, external_record_id: str | None) -> str | None:
    """The ``<path>`` of a record id ``<container>/<path>``; None when the id is not in that container."""
    if not container_name or not external_record_id:
        return None
    prefix = f"{container_name}/"
    if not external_record_id.startswith(prefix):
        return None
    return external_record_id[len(prefix):] or None


@dataclass(frozen=True)
class PathRules:
    """How a store compares folder names: what a value and a path are matched as.

    Matching only. Listing calls and record ids keep the spelled path
    (``FolderScope.spelled_root`` and ``spelled_child``).
    """

    casefold: bool = False
    nfc: bool = False
    # Windows and Azure Files names cannot hold `*`, `?` or `\`, so there `\` separates
    # folders; on an object store it is a character, and escapes `*` and `?`.
    backslash_separates: bool = False

    @property
    def is_identity(self) -> bool:
        return not (self.casefold or self.nfc or self.backslash_separates)

    def normalise(self, s: str) -> str:
        if self.backslash_separates:
            s = s.replace("\\", "/")
        if self.nfc:
            s = unicodedata.normalize("NFC", s)
        if self.casefold:
            s = s.casefold()
            if self.nfc:
                # casefold can leave a string that is no longer NFC.
                s = unicodedata.normalize("NFC", s)
        return s


OBJECT_STORE_PATHS = PathRules()
FILE_SHARE_PATHS = PathRules(casefold=True, nfc=True, backslash_separates=True)
RULES_BY_PATH_STYLE = {"posix": OBJECT_STORE_PATHS, "windows": FILE_SHARE_PATHS}


class FolderRole(Enum):
    """What a walk does with a folder."""

    KEEP = "keep"  # the folder is synced: write its record and walk it
    PASS_THROUGH = "pass_through"  # it may lead to a synced folder: walk it, hold its record back
    SKIP = "skip"  # nothing under it is synced: do not list it


def _has_wildcard(text: str, *, escapes: bool) -> bool:
    """Whether ``text`` holds a ``*`` or ``?`` that is not escaped (read as ``name_regex`` reads it)."""
    i = 0
    while i < len(text):
        if escapes and text[i] == "\\" and text[i + 1:i + 2] in ("*", "?"):
            i += 2
            continue
        if text[i] in "*?":
            return True
        i += 1
    return False


def _unescape(text: str) -> str:
    """``a\\*b`` -> ``a*b``; any other backslash stays."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and text[i + 1:i + 2] in ("*", "?"):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _entry(value: str, rules: PathRules) -> str:
    """A saved value as written, mapped to ``/`` separators, without outer slashes."""
    text = value.strip()
    if rules.backslash_separates:
        text = text.replace("\\", "/")
    return text.strip("/")


def _drop_nested(folders: set[str]) -> tuple[str, ...]:
    return tuple(sorted(f for f in folders if not any(f != o and f.startswith(o) for o in folders)))


@dataclass(frozen=True)
class FolderPattern:
    """One folder value holding ``*``, ``?`` or ``**``, anchored at the top of the store."""

    raw: str
    listing_prefix: str  # the fixed start before the first wildcard segment, plus "/"; "" for none
    segments: tuple[re.Pattern[str] | None, ...]  # None is a whole-segment "**"
    # State j and every state after it reached through "**" segments, worked out once.
    _closures: tuple[frozenset[int], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        closures: list[frozenset[int]] = []
        for start in range(len(self.segments) + 1):
            j = start
            reach = {j}
            while j < len(self.segments) and self.segments[j] is None:
                j += 1
                reach.add(j)
            closures.append(frozenset(reach))
        object.__setattr__(self, "_closures", tuple(closures))

    @classmethod
    def parse(cls, entry: str, rules: PathRules) -> FolderPattern | None:
        """``entry`` is a value after ``_entry``; None when every segment is ``**`` (the whole store)."""
        escapes = not rules.backslash_separates
        texts = entry.split("/")
        if all(t == "**" for t in texts):
            return None
        segments = tuple(
            None if t == "**" else name_regex(rules.normalise(t), escapes=escapes) for t in texts
        )
        fixed: list[str] = []
        for t in texts:
            if t == "**" or _has_wildcard(t, escapes=escapes):
                break
            fixed.append(_unescape(t) if escapes else t)
        return cls(entry, "/".join(fixed) + "/" if fixed else "", segments)

    def walk(self, parts: list[str]) -> tuple[bool, bool]:
        """(full, open) over normalised folder names: whether every segment was matched by
        some prefix of ``parts``, and whether a match is still possible below them."""
        m = len(self.segments)
        closures = self._closures
        states = closures[0]
        full = m in states
        for name in parts:
            moved: set[int] = set()
            for j in states:
                if j == m:
                    continue
                segment = self.segments[j]
                if segment is None:
                    moved |= closures[j]
                elif segment.fullmatch(name):
                    moved |= closures[j + 1]
            if not moved:
                return full, False
            states = moved
            full = full or m in states
        return full, any(j < m for j in states)

    def full_at(self, parts: list[str]) -> bool:
        return self.walk(parts)[0]

    def open_at(self, parts: list[str]) -> bool:
        return self.walk(parts)[1]


class _SpellingNode:
    __slots__ = ("children", "is_literal", "typed")

    def __init__(self, typed: str) -> None:
        self.typed = typed
        self.children: dict[str, _SpellingNode] = {}
        self.is_literal = False


@dataclass(frozen=True, eq=False)
class FolderScope:
    """Folder prefixes (each ending in ``/``), folder patterns, and whether they are excluded.

    ``folders`` keeps the literal values as before patterns existed, so ``key``
    and ``describe`` of a literal-only scope are unchanged. ``rules`` say how
    names compare; with file-share rules a value and a path match after
    ``rules.normalise``.
    """

    folders: tuple[str, ...] = ()
    exclude: bool = False
    patterns: tuple[FolderPattern, ...] = ()
    rules: PathRules = OBJECT_STORE_PATHS
    # The literal values as listing paths ("a/b"), in saved order.
    spellings: tuple[str, ...] = ()

    _match_folders: tuple[str, ...] = field(init=False, repr=False, compare=False)
    _first_spelling: dict[str, str] = field(init=False, repr=False, compare=False)
    _trie: _SpellingNode = field(init=False, repr=False, compare=False)
    _file_memo: list[tuple[str | None, bool]] = field(init=False, repr=False, compare=False)
    _identity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        literal_forms = self._literal_forms()
        if self.rules.is_identity or self.folders in ((), ("",)):
            match_folders = self.folders
        else:
            normal = (self.rules.normalise(f).strip("/") for f in literal_forms)
            match_folders = _drop_nested({f"{n}/" if n else "" for n in normal})
        first: dict[str, str] = {}
        trie = _SpellingNode("")
        for form in literal_forms:
            if not form:
                continue
            first.setdefault(self.rules.normalise(form), form)
            node = trie
            for part in form.split("/"):
                node = node.children.setdefault(self.rules.normalise(part), _SpellingNode(part))
            node.is_literal = True
        object.__setattr__(self, "_match_folders", match_folders)
        object.__setattr__(self, "_first_spelling", first)
        object.__setattr__(self, "_trie", trie)
        object.__setattr__(self, "_file_memo", [(None, False)])
        # What decides behaviour, whatever the order or repetition of the saved values:
        # the folders, the patterns, the rules, and on a share the spelling each folder
        # above or at a literal takes (the first saved one).
        spelled: list[tuple[tuple[str, ...], str, bool]] = []
        if not (self.rules.is_identity or self.exclude):
            stack: list[tuple[tuple[str, ...], _SpellingNode]] = [((), trie)]
            while stack:
                path, node = stack.pop()
                for key, child in node.children.items():
                    spelled.append(((*path, key), child.typed, child.is_literal))
                    stack.append(((*path, key), child))
        object.__setattr__(self, "_identity", (
            self.folders, self.exclude, tuple(sorted({p.raw for p in self.patterns})), self.rules,
            tuple(sorted(spelled)),
        ))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FolderScope):
            return NotImplemented
        return self._identity == other._identity

    def __hash__(self) -> int:
        return hash(self._identity)

    def _literal_forms(self) -> tuple[str, ...]:
        if self.spellings:
            return self.spellings
        return tuple(f[:-1] if f.endswith("/") else f for f in self.folders)

    @classmethod
    def from_filters(
        cls,
        sync_filters: FilterCollection | None,
        *,
        rules: PathRules = OBJECT_STORE_PATHS,
        wildcards: bool = False,
    ) -> FolderScope:
        """The scope of the ``folder_paths`` filter.

        Without ``wildcards`` every value is a literal folder, as it always was.
        With them a value holding ``*``, ``?`` or a ``**`` segment is a pattern;
        on an object store ``\\*`` and ``\\?`` are a literal ``*`` and ``?``.
        """
        folder_filter = sync_filters.get(SyncFilterKey.FOLDER_PATHS) if sync_filters else None
        if not folder_filter or folder_filter.is_empty():
            return cls(rules=rules)
        raw = folder_filter.value
        values = [str(v) for v in (raw if isinstance(raw, list) else [raw]) if v is not None]
        exclude = folder_filter.operator_value == "not_in"
        escapes = wildcards and not rules.backslash_separates

        literals: list[str] = []  # values as main read them, escapes resolved
        spellings: list[str] = []
        pattern_entries: list[str] = []
        for value in values:
            entry = _entry(value, rules)
            if wildcards and _has_wildcard(entry, escapes=escapes):
                if all(part == "**" for part in entry.split("/")):
                    literals.append("")
                    spellings.append("")
                else:
                    pattern_entries.append(entry)
                continue
            literals.append(_unescape(value) if escapes else value)
            spellings.append(_unescape(entry) if escapes else entry)

        folders = {_as_folder(v) for v in literals}
        if "" in folders or "" in spellings:
            # The root folder was named: Include covers everything, Exclude nothing.
            return cls(("",), True, rules=rules) if exclude else cls(rules=rules)
        match_folders = {f"{rules.normalise(s)}/" for s in spellings}
        patterns: list[FolderPattern] = []
        for entry in dict.fromkeys(pattern_entries):
            pattern = FolderPattern.parse(entry, rules)
            assert pattern is not None  # an all-"**" value was read as the root above
            # A pattern whose fixed start lies in a literal folder adds nothing.
            fixed = rules.normalise(pattern.listing_prefix)
            if not any(fixed.startswith(f) for f in match_folders):
                patterns.append(pattern)
        # A folder inside another listed folder adds nothing.
        return cls(_drop_nested(folders), exclude, tuple(patterns), rules, tuple(spellings))

    @property
    def is_everything(self) -> bool:
        return not self.folders and not self.patterns

    @property
    def list_prefixes(self) -> list[str]:
        """Prefixes to list the store with; ``[""]`` means list all of it.

        Excluded folders are listed and then skipped, since the store APIs can
        only narrow a listing to a prefix, not leave one out. No prefix lies
        inside another, so nothing is listed twice.
        """
        if self.is_everything or self.exclude:
            return [""]
        candidates = [f"{form}/" for form in self._literal_forms()]
        candidates += [p.listing_prefix for p in self.patterns]
        if "" in candidates:
            return [""]
        normal = [self.rules.normalise(c) for c in candidates]
        kept: list[str] = []
        seen: set[str] = set()
        for candidate, n in zip(candidates, normal):
            if n in seen or any(len(o) < len(n) and n.startswith(o) for o in normal):
                continue
            seen.add(n)
            kept.append(candidate)
        return sorted(kept)

    def _parts(self, folder: str) -> list[str]:
        return [p for p in folder.split("/") if p]

    def includes_file(self, path: str) -> bool:
        """Whether a file (its path within the store) is synced."""
        if self.is_everything:
            return True
        path = self.rules.normalise(path)
        inside = any(path.lstrip("/").startswith(f) for f in self._match_folders)
        if not inside and self.patterns:
            parent = path.lstrip("/").rsplit("/", 1)[0] if "/" in path.lstrip("/") else ""
            # One tuple, read once and replaced whole, so a scope shared by threads
            # never answers for another parent.
            memo_parent, memo_inside = self._file_memo[0]
            if memo_parent == parent:
                inside = memo_inside
            else:
                parts = self._parts(parent)
                inside = any(p.full_at(parts) for p in self.patterns)
                self._file_memo[0] = (parent, inside)
        return not inside if self.exclude else inside

    def folder_role(self, path: str) -> FolderRole:
        """KEEP, PASS_THROUGH (it may lead to a synced folder) or SKIP, for a folder path."""
        if self.is_everything:
            return FolderRole.KEEP
        folder = _as_folder(self.rules.normalise(path))
        full = any(folder.startswith(f) for f in self._match_folders)
        leads_on = False
        if not full and self.patterns:
            parts = self._parts(folder)
            for pattern in self.patterns:
                pattern_full, pattern_open = pattern.walk(parts)
                if pattern_full:
                    full = True
                    break
                leads_on = leads_on or pattern_open
        if self.exclude:
            return FolderRole.SKIP if full else FolderRole.KEEP
        if full:
            return FolderRole.KEEP
        if leads_on or any(f.startswith(folder) for f in self._match_folders):
            return FolderRole.PASS_THROUGH
        return FolderRole.SKIP

    def includes_folder(self, path: str) -> bool:
        """Whether a folder record is kept.

        With Include, the folders above a chosen folder are kept too, so the
        tree still leads to it; with Exclude, an excluded folder and everything
        under it is dropped.
        """
        return self.folder_role(path) is not FolderRole.SKIP

    def _spells(self) -> bool:
        return not (self.rules.is_identity or self.exclude or self.is_everything)

    def spelled_root(self, prefix: str) -> str:
        """The listing path to walk ``prefix`` (from ``list_prefixes``) from.

        A literal folder is walked from its own saved text. Above a literal,
        each folder is spelled as the first saved literal through it spells
        it, so a record id there is the one that literal's walk writes.
        """
        if not self._spells():
            return prefix
        body = prefix[:-1] if prefix.endswith("/") else prefix
        if not body:
            return prefix
        spelled = self._first_spelling.get(self.rules.normalise(body))
        if spelled is None:
            out: list[str] = []
            node: _SpellingNode | None = self._trie
            for part in body.split("/"):
                node = node.children.get(self.rules.normalise(part)) if node else None
                out.append(node.typed if node else part)
            spelled = "/".join(out)
        return spelled + prefix[len(body):]

    def spelled_child(self, parent: str, name: str) -> str:
        """The path of the entry ``name`` listed in ``parent`` (itself already spelled).

        Inside a literal folder the server's name is kept, as when that literal
        is the root of a walk. Above one, the name is spelled as the first saved
        literal through it spells it.
        """
        joined = f"{parent}/{name}" if parent else name
        if not self._spells():
            return joined
        node = self._trie
        for part in parent.split("/") if parent else ():
            if node.is_literal:
                return joined
            child = node.children.get(self.rules.normalise(part))
            if child is None:
                return joined
            node = child
        if node.is_literal:
            return joined
        child = node.children.get(self.rules.normalise(name))
        if child is None:
            return joined
        return f"{parent}/{child.typed}" if parent else child.typed

    def tracker(self) -> MatchTracker:
        return MatchTracker(self)

    def key(self) -> str:
        """A stable string for this scope, to tell whether it has changed."""
        # JSON, not a joined string: folder names may contain any separator.
        if not self.patterns:
            return json.dumps({"exclude": self.exclude, "folders": sorted(self.folders)})
        return json.dumps({
            "exclude": self.exclude,
            "folders": sorted(self.folders),
            "patterns": sorted(p.raw for p in self.patterns),
        })

    def describe(self) -> str:
        if self.is_everything:
            return "all folders"
        names = ", ".join([f or "/" for f in self.folders] + [p.raw for p in self.patterns])
        return f"all folders except {names}" if self.exclude else f"only {names}"


class MatchTracker:
    """Which folder patterns of a scope matched something during one container's walk.

    Under Include a pattern matches a kept folder or a kept file's folder;
    under Exclude, an excluded one. Literal folders are not tracked.
    """

    def __init__(self, scope: FolderScope) -> None:
        self._scope = scope
        self._matched: set[str] = set()

    def _note(self, folder: str) -> None:
        waiting = [p for p in self._scope.patterns if p.raw not in self._matched]
        if not waiting:
            return
        parts = self._scope._parts(_as_folder(self._scope.rules.normalise(folder)))
        self._matched.update(p.raw for p in waiting if p.full_at(parts))

    def note_folder(self, path: str, role: FolderRole) -> None:
        if role is (FolderRole.SKIP if self._scope.exclude else FolderRole.KEEP):
            self._note(path)

    def note_file(self, path: str) -> None:
        path = path.lstrip("/")
        self._note(path.rsplit("/", 1)[0] if "/" in path else "")

    def matched(self) -> frozenset[str]:
        return frozenset(self._matched)


_MAX_FOLDER_VALUE_LENGTH = 1024
_MAX_DOUBLE_STAR_SEGMENTS = 4
_MAX_STARS_IN_A_NAME = 16

# English text the server answers with; the dashboard shows its own translation by code.
FOLDER_PATH_MESSAGES = {
    "climbs_out": 'Folder "{v}" must be a path from the top of the store, without "..".',
    "empty_segment": 'Folder "{v}" has an empty folder name between two slashes. Remove the extra "/".',
    "excludes_everything": 'Excluding "{v}" leaves nothing to sync. Remove it, or switch to Include.',
    "too_long": f'Folder "{{v}}" is longer than {_MAX_FOLDER_VALUE_LENGTH} characters.',
    "unc_path": 'Folder "{v}" looks like a network path. Start at the top of the share, without \\\\server\\share.',
    "double_star_in_name": (
        'In folder "{v}", "**" must be a whole folder name, as in reports/**/2026. Use "*" for part of a name.'
    ),
    "too_many_double_stars": f'Folder "{{v}}" uses "**" more than {_MAX_DOUBLE_STAR_SEGMENTS} times. Use fewer.',
    "too_many_stars": f'A folder name in "{{v}}" has more than {_MAX_STARS_IN_A_NAME} "*". Use fewer.',
}


def _climbs_out(value: str) -> bool:
    return any(part.strip() == ".." for part in value.replace("\\", "/").split("/"))


def _star_runs(name: str, *, escapes: bool) -> list[int]:
    """The lengths of the runs of unescaped ``*`` in one folder name."""
    runs: list[int] = []
    run = 0
    i = 0
    while i < len(name):
        if escapes and name[i] == "\\" and name[i + 1:i + 2] in ("*", "?"):
            i += 2
        elif name[i] == "*":
            run += 1
            i += 1
            continue
        else:
            i += 1
        if run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return runs


def folder_value_problems(value: str, *, exclude: bool, path_style: str = "posix", wildcards: bool = False) -> list[str]:
    """The codes a new folder value is refused for (``FOLDER_PATH_MESSAGES``), in that order.

    ``climbs_out`` is included; it is the only code a value saved before is
    still held to.
    """
    windows = path_style == "windows"
    codes: list[str] = []
    if _climbs_out(value):
        codes.append("climbs_out")
    text = value.strip()
    mapped = text.replace("\\", "/") if windows else text
    body = mapped.strip("/")
    names = body.split("/")
    if "//" in body:
        codes.append("empty_segment")
    if exclude and all(n in ("", "**") for n in names):
        codes.append("excludes_everything")
    if len(value) > _MAX_FOLDER_VALUE_LENGTH:
        codes.append("too_long")
    if windows and text.startswith("\\\\") and len([n for n in names if n]) >= 2:
        codes.append("unc_path")
    if wildcards:
        escapes = not windows
        if any(n != "**" and any(r > 1 for r in _star_runs(n, escapes=escapes)) for n in names):
            codes.append("double_star_in_name")
        if sum(n == "**" for n in names) > _MAX_DOUBLE_STAR_SEGMENTS:
            codes.append("too_many_double_stars")
        if any(n != "**" and sum(_star_runs(n, escapes=escapes)) > _MAX_STARS_IN_A_NAME for n in names):
            codes.append("too_many_stars")
    return codes


def _folder_entry(sync_values: object) -> dict | None:
    entry = sync_values.get(SyncFilterKey.FOLDER_PATHS.value) if isinstance(sync_values, dict) else None
    return entry if isinstance(entry, dict) else None


def _entry_values(entry: dict) -> list[str]:
    raw = entry.get("value")
    return [str(v) for v in (raw if isinstance(raw, list) else [raw]) if v is not None]


def _entry_operator(entry: dict) -> str:
    return str(entry.get("operator") or ListOperator.IN.value)


def saved_folder_values(saved_sync_values: object, sync_values: object) -> frozenset[str]:
    """The folder values saved before this save, when it keeps the operator they were saved under.

    Such a value keeps working as it did, so only values outside this set are
    held to the newer rules.
    """
    saved, new = _folder_entry(saved_sync_values), _folder_entry(sync_values)
    if saved is None or new is None or _entry_operator(saved) != _entry_operator(new):
        return frozenset()
    return frozenset(v.strip() for v in _entry_values(saved))


def folder_path_problems(
    sync_values: dict[str, object],
    *,
    path_style: str = "posix",
    wildcards: bool = False,
    stored: Container[str] | None = None,
    new_rules: bool = False,
) -> list[str]:
    """Why the folder filter in ``sync_values`` cannot be saved.

    A value that would climb out of the bucket, container or share is always
    refused. With ``new_rules`` (a save), values not in ``stored`` are also
    checked for the rest of ``FOLDER_PATH_MESSAGES``; saved values never are,
    so a value saved before these rules still enables and saves.
    """
    entry = _folder_entry(sync_values)
    if entry is None:
        return []
    values = _entry_values(entry)
    for value in values:
        if _climbs_out(value):
            return [FOLDER_PATH_MESSAGES["climbs_out"].format(v=value)]
    if not new_rules:
        return []
    exclude = _entry_operator(entry) == ListOperator.NOT_IN.value
    kept = stored if stored is not None else frozenset()
    return [
        FOLDER_PATH_MESSAGES[code].format(v=value)
        for value in values
        if value.strip() not in kept
        for code in folder_value_problems(value, exclude=exclude, path_style=path_style, wildcards=wildcards)
    ]


class CleanupResult(NamedTuple):
    removed: int
    failed: int


async def _records_in(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
) -> AsyncIterator[tuple[Record, str]]:
    """This connector's records in ``container_name`` with their paths, a page at a time.

    Record ids are ``<container>/<path>``. Trashed records are included: a removal
    scan must still delete one the source no longer has, and a listed object whose
    record is in the trash is a known object, not an unrecorded one.
    """
    prefix = f"{container_name}/"
    after_key = None
    while True:
        page = await data_entities_processor.get_records_in_record_group(
            connector_id, container_name, _PAGE_SIZE, after_key, visibility=RecordVisibility.ALL
        )
        for record in page:
            external_id = record.external_record_id or ""
            if external_id.startswith(prefix):
                yield record, external_id[len(prefix):]
        if len(page) < _PAGE_SIZE:
            break
        after_key = page[-1].id


async def recorded_ids(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
) -> set[str]:
    """The ids of this connector's records in ``container_name``, read before a listing."""
    return {
        record.external_record_id
        async for record, _ in _records_in(data_entities_processor, connector_id, container_name)
    }


async def recorded_record_ids(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
) -> dict[str, str]:
    """External id -> record id of this connector's records in ``container_name``, read before a listing.

    Kept for the removal pass too (``remove_recorded_not_listed``), so a sync reads the container's
    records once instead of before and again after the listing.
    """
    return {
        record.external_record_id: record.id
        async for record, _ in _records_in(data_entities_processor, connector_id, container_name)
    }


async def _remove_records(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    doomed: Callable[[Record, str], bool],
    reason: str,
    logger: logging.Logger,
) -> CleanupResult:
    """Delete this connector's records in ``container_name`` whose path ``doomed`` picks."""
    removed = failed = 0
    async for record, path in _records_in(data_entities_processor, connector_id, container_name):
        if not doomed(record, path):
            continue
        try:
            await data_entities_processor.on_record_deleted(record.id)
            removed += 1
        except Exception as e:  # noqa: BLE001 — one failed delete must not stop the rest
            failed += 1
            logger.warning(f"Failed to remove {record.external_record_id} {reason}: {e}")
    if removed:
        logger.info(f"Removed {removed} records in {container_name} {reason}")
    return CleanupResult(removed, failed)


async def remove_records_outside_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    scope: FolderScope,
    logger: logging.Logger,
) -> CleanupResult:
    """Delete this connector's records in ``container_name`` that ``scope`` leaves out.

    Narrowing the folders to sync stops new files from being indexed; this also
    takes out what was indexed before. Folder records are told apart by their
    folder MIME type.
    """
    if scope.is_everything:
        return CleanupResult(0, 0)

    from app.config.constants.arangodb import FOLDER_MIME_TYPES

    def outside(record: Record, path: str) -> bool:
        is_folder = record.mime_type in FOLDER_MIME_TYPES
        return not (scope.includes_folder(path) if is_folder else scope.includes_file(path))

    return await _remove_records(
        data_entities_processor, connector_id, container_name, outside,
        f"outside {scope.describe()}", logger,
    )


def listed_record_ids(container_name: str, path: str) -> set[str]:
    """The record ids a listed object keeps: its own and those of the folders above it.

    Folders are implicit in object keys, so ``a/b/c.txt`` keeps ``a`` and ``a/b``;
    a folder object ``a/b/`` keeps both ``a/b/`` and ``a/b``.
    """
    key = path.lstrip("/")
    parts = [p for p in key.rstrip("/").split("/") if p]
    ids = {f"{container_name}/{'/'.join(parts[:i])}" for i in range(1, len(parts) + 1)}
    if key:
        ids.add(f"{container_name}/{key}")
    return ids


def _under_prefix(path: str, prefix: str) -> bool:
    # A listed prefix "reports/" also covers its own folder record, stored as "reports".
    return path.startswith(prefix) or (bool(prefix) and path == prefix.rstrip("/"))


async def remove_records_not_listed(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    prefixes: list[str],
    listed: set[str],
    logger: logging.Logger,
) -> CleanupResult:
    """Delete this connector's records under ``prefixes`` that their listings did not keep.

    This is how a deletion at the source, or a file a sync filter now excludes,
    leaves the index. ``listed`` holds the ids from ``listed_record_ids`` for
    every object the listings returned and the filters keep. Call it once, after
    every prefix of the container was listed from its first page to its last
    without an error: anything a partial listing missed would be deleted, and a
    rename into a prefix listed later is still stored under its old path until
    that prefix is processed.
    """
    return await _remove_records(
        data_entities_processor, connector_id, container_name,
        lambda record, path: any(_under_prefix(path, p) for p in prefixes)
        and f"{container_name}/{path}" not in listed,
        "missing from the latest listing", logger,
    )


async def remove_recorded_not_listed(
    data_entities_processor: DataSourceEntitiesProcessor,
    container_name: str,
    prefixes: list[str],
    recorded: dict[str, str],
    listed: set[str],
    written: Container[str],
    logger: logging.Logger,
) -> CleanupResult:
    """``remove_records_not_listed`` from the records read before the listing (``recorded_record_ids``).

    A record written during this sync (``written``: record ids) is never removed, though its
    id may still be under its old path in ``recorded``: it moved, or was renamed, to a listed blob.
    Records created during the sync are listed, so they need no second read either.
    """
    removed = failed = 0
    prefix = f"{container_name}/"
    for external_id, record_id in recorded.items():
        path = external_id[len(prefix):]
        if external_id in listed or record_id in written or not any(_under_prefix(path, p) for p in prefixes):
            continue
        try:
            await data_entities_processor.on_record_deleted(record_id)
            removed += 1
        except Exception as e:  # one failed delete must not stop the rest
            failed += 1
            logger.warning(f"Failed to remove {external_id} missing from the latest listing: {e}")
    if removed:
        logger.info(f"Removed {removed} records in {container_name} missing from the latest listing")
    return CleanupResult(removed, failed)


def _names(names_filter: Filter) -> frozenset[str]:
    raw = names_filter.value if isinstance(names_filter.value, list) else [names_filter.value]
    return frozenset(name for name in raw if isinstance(name, str) and name)


async def _saved_filter(
    config_service: ConfigurationService, connector_id: str, filter_name: str, logger: logging.Logger,
) -> tuple[FilterCollection, Filter] | None:
    """The saved sync filters and their ``filter_name`` filter; None unless that filter was read and is set.

    Read here rather than through ``load_connector_filters``, which answers a
    failed or empty read with no filters, the same as a filter left unset.
    """
    try:
        config = await config_service.get_config(f"/services/connectors/{connector_id}/config")
    except Exception as e:  # an unreadable config removes nothing
        logger.warning(f"Not removing de-selected {filter_name}: the connector config could not be read: {e}")
        return None
    if not isinstance(config, dict) or not config.get("enabled", True):
        return None
    filters = config.get("filters")
    sync = filters.get("sync") if isinstance(filters, dict) else None
    values = sync.get("values") if isinstance(sync, dict) else None
    if not isinstance(values, dict):
        return None
    saved = FilterCollection.from_dict(values, logger)
    names_filter = saved.get(filter_name)
    if names_filter is None or names_filter.is_empty():
        return None
    return saved, names_filter


async def remove_deselected_containers(
    data_entities_processor: DataSourceEntitiesProcessor,
    config_service: ConfigurationService,
    connector_id: str,
    filter_name: str,
    sync_filters: FilterCollection | None,
    logger: logging.Logger,
) -> None:
    """Delete the records, then the record group, of each stored bucket or container
    that the saved ``filter_name`` filter leaves out: one no longer named under In,
    or newly named under Not in.

    ``sync_filters`` are the filters this sync runs with. The saved filter is read
    again and must match them, so a failed or empty read, a filter left unset (all
    of them are synced) or one edited mid-sync removes nothing. What the cloud
    API lists plays no part, so a failed listing is never taken for de-selection.
    """
    current = sync_filters.get(filter_name) if sync_filters else None
    if current is None or current.is_empty():
        return
    read = await _saved_filter(config_service, connector_id, filter_name, logger)
    if read is None:
        return
    saved, saved_filter = read
    if saved_filter.operator_value != current.operator_value or _names(saved_filter) != _names(current):
        return

    try:
        stored = await stored_containers(data_entities_processor, connector_id)
    except Exception as e:  # retried on the next sync
        logger.warning(f"Not removing de-selected {filter_name}: the stored ones could not be read: {e}")
        return
    stale = sorted(name for name in stored if not name_passes_filter(saved, filter_name, name))
    for container_name in stale:
        await remove_container(
            data_entities_processor, connector_id, container_name,
            f"now that the {filter_name} filter leaves {container_name} out", logger,
        )


async def stored_containers(data_entities_processor: DataSourceEntitiesProcessor, connector_id: str) -> set[str]:
    """The names of this connector's stored bucket or container record groups."""
    from app.config.constants.arangodb import CollectionNames
    from app.models.entities import RecordGroupType

    groups = await data_entities_processor.get_nodes_by_filters(
        collection=CollectionNames.RECORD_GROUPS.value,
        filters={"connectorId": connector_id, "groupType": RecordGroupType.BUCKET.value},
    )
    return {g.get("externalGroupId") for g in groups if isinstance(g, dict)} - {None, ""}


async def remove_container(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    reason: str,
    logger: logging.Logger,
) -> bool:
    """Delete every record of ``container_name``, then its record group; what fails is retried next sync.
    Returns whether the container is gone."""
    try:
        result = await _remove_records(
            data_entities_processor, connector_id, container_name, lambda record, path: True, reason, logger,
        )
    except Exception as e:  # retried on the next sync
        logger.warning(f"Could not read the records of {container_name} to remove: {e}")
        return False
    if result.failed:
        logger.warning(f"{result.failed} records of {container_name} could not be removed; retrying next sync")
        return False
    # The group goes last: while it stays, the next sync finds the container again.
    if not await data_entities_processor.on_record_group_deleted(container_name, connector_id):
        logger.warning(f"Could not remove the record group of {container_name}; retrying next sync")
        return False
    return True


async def clean_up_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    sync_point: ISyncPoint,
    connector_id: str,
    container_name: str,
    scope: FolderScope,
    logger: logging.Logger,
) -> None:
    """Remove records outside ``scope`` unless this scope was already cleaned up.

    The scope last cleaned without a failed delete is kept in a sync point, so a
    failed cleanup is retried on the next sync and an unchanged scope is not
    rescanned. Editing the filters deletes the connector's sync points, which
    clears it.
    """
    if scope.is_everything:
        return

    from app.connectors.core.base.sync_point.sync_point import (
        generate_record_sync_point_key,
    )
    from app.models.entities import RecordType

    key = generate_record_sync_point_key(RecordType.FILE.value, "folder_scope", container_name)
    cleaned = await sync_point.read_sync_point(key)
    if cleaned and cleaned.get("scope") == scope.key():
        return
    result = await remove_records_outside_scope(
        data_entities_processor, connector_id, container_name, scope, logger
    )
    if result.failed:
        logger.warning(
            f"{result.failed} records in {container_name} outside {scope.describe()} "
            "could not be removed; retrying next sync"
        )
        return
    await sync_point.update_sync_point(key, {"scope": scope.key()})

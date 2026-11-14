"""Generated network-share walks under literal folder filters, and the golden file they are checked against.

Each world is a seeded random share tree plus a literal ``folder_paths`` filter
(no ``*`` or ``?``). Walking it records what a later change to folder matching
must keep for literal values: the ids the walk saw, the records it wrote in
order with their parents, the directories it listed and stat-ed, and what it
could not read. Half the shares resolve names as a case-insensitive server does.

Regenerate only on purpose, from a checkout whose walk is the reference::

    cd backend/python && PYTHONPATH=. python tests/unit/connectors/sources/network_share_literal_walks.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
    SyncFilterKey,
)
from app.connectors.sources.network_share.entry import DirectoryEntry
from app.connectors.sources.network_share.errors import DirectoryListingError
from app.connectors.sources.network_share.record_mapper import RecordMapper
from app.connectors.sources.network_share.walker import ShareWalker

GOLDEN = Path(__file__).parent / "fixtures" / "network_share_literal_walks.json"
SHARE = "docs"
WORLDS = 200
SEED = 20261112
# Names chosen to collide: prefixes of each other, case and NFC/NFD twins, brackets, a space.
NAMES = ("a", "ab", "A", "b", "caf\u00e9", "cafe\u0301", "Project [2024]", "x y", ".hidden", "node_modules")
BASE_TIME = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _key(name: str, insensitive: bool) -> str:
    return unicodedata.normalize("NFC", name).casefold() if insensitive else name


class GoldenShare:
    """An in-memory share. ``insensitive`` resolves path names by case and NFC form, as Windows and Samba do."""

    def __init__(self, tree: dict[str, list[DirectoryEntry]], *, insensitive: bool, fail: set[str]) -> None:
        self.tree = tree
        self.insensitive = insensitive
        self.fail = fail
        self.list_calls: list[str] = []
        self.stat_calls: list[str] = []

    def _resolve(self, path: str) -> str | None:
        """The stored directory a typed path names, or None."""
        resolved = ""
        for part in (p for p in path.split("/") if p):
            entries = self.tree.get(resolved)
            if entries is None:
                return None
            match = next(
                (e for e in entries if e.is_directory and _key(e.name, self.insensitive) == _key(part, self.insensitive)),
                None,
            )
            if match is None:
                return None
            resolved = f"{resolved}/{match.name}" if resolved else match.name
        return resolved

    async def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        self.list_calls.append(path)
        resolved = self._resolve(path)
        if resolved is None or resolved not in self.tree:
            raise FileNotFoundError(path)
        if resolved in self.fail:
            raise DirectoryListingError(share, path, "listing failed")
        return list(self.tree[resolved])

    async def stat(self, share: str, path: str, *, follow: bool = True) -> DirectoryEntry | None:
        self.stat_calls.append(path)
        if not path:
            return DirectoryEntry("", True, False, 0, BASE_TIME, BASE_TIME, 1)
        parent, _, name = path.rpartition("/")
        resolved = self._resolve(parent)
        if resolved is None:
            return None
        return next(
            (e for e in self.tree.get(resolved, []) if _key(e.name, self.insensitive) == _key(name, self.insensitive)),
            None,
        )


def _tree(rng: random.Random, *, insensitive: bool) -> dict[str, list[DirectoryEntry]]:
    tree: dict[str, list[DirectoryEntry]] = {}
    ids = iter(range(2, 10**6))

    def fill(path: str, depth: int) -> None:
        entries: list[DirectoryEntry] = []
        names = rng.sample(NAMES, rng.randint(1, 4))
        if insensitive:
            # Such a server cannot hold two names that differ only in case or NFC form.
            taken: set[str] = set()
            names = [n for n in names if not (_key(n, True) in taken or taken.add(_key(n, True)))]
        for name in names:
            stamp = BASE_TIME + timedelta(minutes=next(ids))
            if depth < 3 and rng.random() < 0.6:
                entries.append(DirectoryEntry(name, True, False, 0, stamp, stamp, next(ids)))
                fill(f"{path}/{name}" if path else name, depth + 1)
            else:
                entries.append(DirectoryEntry(f"{name}.txt", False, False, 4, stamp, stamp, next(ids)))
        tree[path] = entries

    fill("", 0)
    return tree


def _typed(rng: random.Random, folders: list[str]) -> str:
    """A folder value as an admin might type it: a real folder, respelled or decorated, or one that does not exist."""
    roll = rng.random()
    if roll < 0.06:
        return rng.choice(["/", ""])
    if roll < 0.14 or not folders:
        return rng.choice(["missing", "a/missing", "A/b", "ab/a"])
    value = rng.choice(folders)
    roll = rng.random()
    if roll < 0.15:
        value = value.swapcase()
    elif roll < 0.25:
        value = unicodedata.normalize("NFD", value)
    elif roll < 0.35:
        value = value.replace("/", "\\")
    if rng.random() < 0.2:
        value = f"/{value}"
    if rng.random() < 0.2:
        value = f"{value}/"
    return value


def worlds() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    out = []
    for index in range(WORLDS):
        insensitive = rng.random() < 0.5
        tree = _tree(rng, insensitive=insensitive)
        folders = [p for p in tree if p]
        values = [_typed(rng, folders) for _ in range(rng.randint(1, 3))] if rng.random() < 0.9 else []
        out.append({
            "world": index,
            "tree": {
                path: [[e.name, e.is_directory, e.file_id] for e in entries] for path, entries in sorted(tree.items())
            },
            "insensitive": insensitive,
            "values": values,
            "exclude": rng.random() < 0.3,
            "fail": sorted(rng.sample(folders, 1)) if folders and rng.random() < 0.2 else [],
        })
        out[-1]["n_neutral"] = n_neutral(out[-1])
    return out


def share_n(path: str) -> str:
    """How file shares compare names: ``\\`` separates folders, case and Unicode form do not count."""
    return unicodedata.normalize("NFC", path.replace("\\", "/").casefold())


def _as_folder(path: str) -> str:
    cleaned = path.strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def _relations(path: str, folders: list[str]) -> list[tuple[bool, bool, bool]]:
    folder = _as_folder(path)
    return [(folder.startswith(f), f.startswith(folder), path.startswith(f)) for f in folders]


def n_neutral(world: dict[str, Any]) -> bool:
    """Whether comparing names as a file share does would decide this world as the raw comparison does.

    Not neutral when the share comparison changes a value (a backslash, or a
    case or Unicode-form variant), which also covers two values that become
    equal or nested, or when a path on the share relates to a value (inside it,
    above it) differently under that comparison. Neutral worlds must replay
    exactly; the others may differ only by a declared delta.
    """
    values = world["values"]
    raw = [_as_folder(v) for v in values]
    normal = [_as_folder(share_n(v)) for v in values]
    if raw != normal:
        return False
    paths = [f"{d}/{name}" if d else name for d, entries in world["tree"].items() for name, _is_dir, _fid in entries]
    # Ids are NFC on main, so NFC is the raw form a path is matched in.
    return all(
        _relations(unicodedata.normalize("NFC", p), raw) == _relations(share_n(p), normal) for p in paths
    )


def _nested_pairs(folders: list[str]) -> set[tuple[int, int]]:
    return {(i, j) for i, f in enumerate(folders) for j, o in enumerate(folders) if i != j and f.startswith(o)}


def delta_kinds(world: dict[str, Any]) -> set[str]:
    """Why comparing names as a file share does may decide ``world`` differently, one kind per step.

    The steps go from the raw comparison to the share's: ``\\`` separates
    (backslash), then Unicode form (unicode_form), then case (case). A step
    counts when it changes a value or how some path relates to the values.
    equal_values: two values name the same folder, or one lies in the other,
    only once compared as a share does.
    """
    values = world["values"]
    paths = [f"{d}/{name}" if d else name for d, entries in world["tree"].items() for name, _is_dir, _fid in entries]
    steps = [
        ("backslash", lambda v: v.replace("\\", "/"), lambda p: p),
        ("unicode_form", lambda v: unicodedata.normalize("NFC", v), lambda p: p),
        ("case", share_n, share_n),
    ]
    kinds: set[str] = set()
    folders = [_as_folder(v) for v in values]
    nfc_paths = [unicodedata.normalize("NFC", p) for p in paths]
    for kind, value_step, path_step in steps:
        next_folders = [_as_folder(value_step(v)) for v in values]
        next_paths = [path_step(p) for p in nfc_paths]
        if next_folders != folders or any(
            _relations(p, folders) != _relations(q, next_folders) for p, q in zip(nfc_paths, next_paths, strict=True)
        ):
            kinds.add(kind)
        values = [value_step(v) for v in values]
        folders, nfc_paths = next_folders, next_paths
    if _nested_pairs([_as_folder(share_n(v)) for v in world["values"]]) != _nested_pairs(
        [_as_folder(v) for v in world["values"]]
    ):
        kinds.add("equal_values")
    return kinds


def _entries(raw: list[list[Any]]) -> list[DirectoryEntry]:
    return [
        DirectoryEntry(name, is_dir, False, 0 if is_dir else 4, BASE_TIME + timedelta(minutes=fid),
                       BASE_TIME + timedelta(minutes=fid), fid)
        for name, is_dir, fid in raw
    ]


async def walk(world: dict[str, Any]) -> dict[str, Any]:
    share = GoldenShare(
        {path: _entries(raw) for path, raw in world["tree"].items()},
        insensitive=world["insensitive"],
        fail=set(world["fail"]),
    )
    filters = FilterCollection(filters=[
        Filter(
            key=SyncFilterKey.FOLDER_PATHS.value,
            value=list(world["values"]),
            type=FilterType.LIST,
            operator=ListOperator.NOT_IN if world["exclude"] else ListOperator.IN,
        )
    ] if world["values"] else [])
    written: list[list[Any]] = []

    async def none(_key: str) -> None:
        return None

    async def flush_upserts(batch: list) -> None:
        written.extend([r.external_record_id, r.parent_external_record_id, r.is_file] for r, _perms in batch)

    async def flush_moves(batch: list) -> None:
        raise AssertionError("a walk with no stored records has no moves")

    walker = ShareWalker(
        data_source=share,
        mapper=RecordMapper(),
        logger=logging.getLogger("test.network_share_literal_walks"),
        connector_name=Connectors.SMB,
        connector_id="smb-golden",
        batch_size=3,
        get_by_external_id=none,
        get_by_revision=none,
        flush_upserts=flush_upserts,
        flush_moves=flush_moves,
        permissions_for=lambda _record: [],
    )
    result = await walker.walk_share(SHARE, filters, FilterCollection())
    return {
        "world": world["world"],
        "seen": sorted(result.seen),
        "written": written,
        "list_calls": share.list_calls,
        "stat_calls": share.stat_calls,
        "complete": result.complete,
        "unread": sorted(result.unread),
    }


def walk_all(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return asyncio.run(_walk_all(cases))


async def _walk_all(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [await walk(world) for world in cases]


def main() -> None:
    logging.disable(logging.CRITICAL)
    cases = worlds()
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(dump(cases, walk_all(cases)), encoding="utf-8")


def dump(cases: list[dict[str, Any]], walks: list[dict[str, Any]]) -> str:
    """One world and its walk per line, so a changed walk shows as a changed line."""
    lines = [json.dumps({"case": c, "walk": w}, ensure_ascii=False) for c, w in zip(cases, walks, strict=True)]
    return "[\n" + ",\n".join(lines) + "\n]\n"


def load() -> list[dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

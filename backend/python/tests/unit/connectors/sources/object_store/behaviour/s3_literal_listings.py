"""Generated S3 syncs under literal folder filters, and the golden file they are checked against.

Each world is a seeded random bucket plus a literal ``folder_paths`` filter (no
``*`` or ``?``). It is synced twice through the real S3 connector over the fake
``list_objects_v2``: once from empty, then after an object is deleted and
another added. The recording holds what a later change to folder matching must
keep for literal values: every listing call, the records written in order, the
records left, what was removed, and the sync point keys with the folder scope
each one stored.

Regenerate only on purpose, from a checkout whose sync is the reference::

    cd backend/python && PYTHONPATH=.:tests/unit/connectors/sources/object_store/behaviour \\
        python tests/unit/connectors/sources/object_store/behaviour/s3_literal_listings.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import unicodedata
from pathlib import Path
from typing import Any

from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    make_connector,
)

GOLDEN = Path(__file__).parents[2] / "fixtures" / "s3_literal_listings.json"
WORLDS = 100
SEED = 20261112
# Segments chosen to collide: prefixes of each other, case and NFC/NFD twins, and the
# characters a folder value keeps literally (brackets, a backslash, a space, a newline).
SEGMENTS = ("a", "ab", "A", "caf\u00e9", "cafe\u0301", "[x]", "r\\s", "x y", "q\n")


def _keys(rng: random.Random) -> list[str]:
    keys: set[str] = set()
    for _ in range(rng.randint(1, 12)):
        folders = [rng.choice(SEGMENTS) for _ in range(rng.randint(0, 3))]
        if rng.random() < 0.15 and folders:
            keys.add("/".join(folders) + "/")  # a folder marker
        else:
            keys.add("/".join([*folders, f"{rng.choice(SEGMENTS)}.txt"]))
    return sorted(keys)


def _typed(rng: random.Random, keys: list[str]) -> str:
    """A folder value as an admin might type it: a real folder, respelled or decorated, or one that does not exist."""
    folders = sorted({k.rsplit("/", 1)[0] for k in keys if "/" in k.rstrip("/")} | {k.rstrip("/") for k in keys if k.endswith("/")})
    roll = rng.random()
    if roll < 0.06:
        return rng.choice(["/", ""])
    if roll < 0.14 or not folders:
        return rng.choice(["missing", "a/missing", "A/a", "a//ab"])
    parts = rng.choice(folders).split("/")
    value = "/".join(parts[: rng.randint(1, len(parts))])
    roll = rng.random()
    if roll < 0.1:
        value = value.swapcase()
    elif roll < 0.2:
        value = unicodedata.normalize("NFD", value)
    elif roll < 0.25:
        value = value[:-1]  # a prefix of a name, which names no folder
    if rng.random() < 0.2:
        value = f"/{value}"
    if rng.random() < 0.2:
        value = f"{value}/"
    return value


def worlds() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    out = []
    for index in range(WORLDS):
        keys = _keys(rng)
        values = [_typed(rng, keys) for _ in range(rng.randint(1, 3))] if rng.random() < 0.9 else []
        out.append({
            "world": index,
            "keys": keys,
            "values": values,
            "exclude": rng.random() < 0.3,
            "delete": rng.choice(keys),
            "add": _keys(rng)[0],
        })
    return out


async def sync(world: dict[str, Any]) -> dict[str, Any]:
    store, db, checkpoints, config = FakeObjectStore(), FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService()
    if world["values"]:
        config.sync_filters["folder_paths"] = {
            "operator": "not_in" if world["exclude"] else "in", "value": list(world["values"]), "type": "list",
        }
    for key in world["keys"]:
        store.put(key)
    connector = make_connector("s3", store, db, checkpoints, config)
    calls: list[list[Any]] = []
    listing = connector.data_source.list_objects_v2

    async def recorded(**kwargs: object) -> object:
        calls.append([kwargs.get("Prefix"), kwargs.get("ContinuationToken"), kwargs.get("MaxKeys")])
        return await listing(**kwargs)

    connector.data_source.list_objects_v2 = recorded
    runs = []
    for run in range(2):
        if run:
            store.delete(world["delete"])
            store.put(world["add"])
        calls.clear()
        db.written.clear()
        db.deleted.clear()
        deleted_paths = {r.id: r.external_record_id for r in db.records.values()}
        await connector.run_sync()
        runs.append({
            "list_calls": list(calls),
            "written": list(db.written),
            "removed": sorted(deleted_paths[i] for i in db.deleted if i in deleted_paths),
            "records": sorted(db.paths()),
            "sync_points": {
                key: value.get("scope") for key, value in sorted(checkpoints.sync_points.items())
            },
        })
    return {"world": world["world"], "runs": runs}


def sync_all(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return asyncio.run(_sync_all(cases))


async def _sync_all(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [await sync(world) for world in cases]


def dump(cases: list[dict[str, Any]], syncs: list[dict[str, Any]]) -> str:
    """One world and its syncs per line, so a changed sync shows as a changed line."""
    lines = [json.dumps({"case": c, "sync": s}, ensure_ascii=False) for c, s in zip(cases, syncs, strict=True)]
    return "[\n" + ",\n".join(lines) + "\n]\n"


def load() -> list[dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def main() -> None:
    # The value tests/conftest.py gives a pytest run; sync points refuse to start without one.
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789abcdef")
    logging.disable(logging.CRITICAL)
    cases = worlds()
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(dump(cases, sync_all(cases)), encoding="utf-8")


if __name__ == "__main__":
    main()

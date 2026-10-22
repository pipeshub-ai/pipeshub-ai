"""Split backend/python/tests/unit into the shards unit-test.yml runs in parallel.

One process ran the whole unit suite: ~5 min collecting 78k tests and ~36 min
running them, so every Python pull request waited ~48 min for one job. The
`python-unit` matrix in `.github/workflows/unit-test.yml` runs one job per shard
here instead, and a later job combines their coverage data for the gate.

A shard owns paths under backend/python. A test file belongs to the shard owning
the deepest path that contains it, so a shard can own a directory while another
owns something inside it. `core` owns `tests/unit` itself: anything no other
shard claims runs there, so a new test directory is never left out, it just
makes `core` longer. Glob entries expand to the files they match.

Run (from backend/python): python3 ../../scripts/unit_shards.py args <shard>
prints the pytest arguments for that shard, one per line.

Measured minutes per shard (serial, under coverage, 2026-10-22) are in the
comments; move paths between shards when one grows far past the others.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parent.parent / "backend" / "python"
UNIT_ROOT = "tests/unit"

SHARDS: dict[str, tuple[str, ...]] = {
    # ~5.5 min: the connector test files directly under sources/.
    "connectors-a": ("tests/unit/connectors/sources/test_*.py",),
    # ~5.3 min: web (3.3), localKB and the smaller connector directories.
    "connectors-b": ("tests/unit/connectors/sources",),
    # ~5.3 min: google (2.7), microsoft, and connectors outside sources/.
    "connectors-c": (
        "tests/unit/connectors",
        "tests/unit/connectors/sources/google",
        "tests/unit/connectors/sources/microsoft",
    ),
    # ~5.4 min
    "agents": ("tests/unit/agents", "tests/unit/agent_loop_lib"),
    # ~4.2 min
    "services": (
        "tests/unit/services",
        "tests/unit/events",
        "tests/unit/sandbox",
        "tests/unit/containers",
        "tests/unit/config",
        "tests/unit/telemetry",
    ),
    # ~5.1 min
    "modules": ("tests/unit/modules", "tests/unit/utils"),
    # ~4.8 min: top-level test files, api, sources, models and the rest.
    "core": (UNIT_ROOT,),
}


def _expand(entry: str, root: Path) -> list[str]:
    if not glob.has_magic(entry):
        return [entry]
    return sorted(
        Path(p).relative_to(root).as_posix() for p in glob.glob(str(root / entry))
    )


def owned_paths(root: Path = PYTHON_ROOT) -> dict[str, str]:
    """Every owned path (globs expanded), mapped to the shard that owns it."""
    owners: dict[str, str] = {}
    for shard, entries in SHARDS.items():
        for entry in entries:
            for path in _expand(entry, root):
                if path in owners:
                    raise ValueError(f"{path} is owned by both {owners[path]} and {shard}")
                owners[path] = shard
    return owners


def _contains(parent: str, child: str) -> bool:
    return child == parent or child.startswith(parent + "/")


def nearest_owner(path: str, owners: dict[str, str], *, strict: bool = False) -> str | None:
    """The deepest owned path containing `path` (excluding `path` itself when strict)."""
    best = None
    for owned in owners:
        if strict and owned == path:
            continue
        if _contains(owned, path) and (best is None or len(owned) > len(best)):
            best = owned
    return best


def _is_collectable(path: Path) -> bool:
    if path.is_dir():
        return path.name != "__pycache__"
    return path.name.startswith("test_") and path.suffix == ".py"


def pytest_args(shard: str, root: Path = PYTHON_ROOT) -> list[str]:
    """Paths to collect plus --ignore for what other shards own inside them.

    --ignore stops pytest descending into a path even to reach a path given on
    the command line under it, so a directory is ignored only when nothing
    inside it is ours. Where another shard's directory holds something of ours,
    the directories above it are passed child by child instead.
    """
    if shard not in SHARDS:
        raise KeyError(f"unknown shard {shard!r}; known: {', '.join(SHARDS)}")
    owners = owned_paths(root)
    positional: list[str] = []
    ignores: list[str] = []

    def inside(path: str) -> list[str]:
        return [p for p in owners if p != path and _contains(path, p)]

    def visit(path: str, owner: str) -> None:
        nested = inside(path)
        theirs = [p for p in nested if owners[p] != shard]
        ours = [p for p in nested if owners[p] == shard]
        if owner == shard and not theirs:
            positional.append(path)
            return
        if owner != shard and not ours:
            return
        # Outermost paths of other shards, each with nothing of ours inside.
        outer = [p for p in theirs if not any(q != p and _contains(q, p) for q in theirs)]
        if owner == shard and not any(_contains(p, q) for p in outer for q in ours):
            positional.append(path)
            ignores.extend(f"--ignore={p}" for p in outer)
            return
        for child in sorted((root / path).iterdir()):
            if _is_collectable(child):
                rel = child.relative_to(root).as_posix()
                visit(rel, owners.get(rel, owner))

    visit(UNIT_ROOT, owners[UNIT_ROOT])
    return positional + sorted(ignores)


def test_files(root: Path = PYTHON_ROOT) -> list[str]:
    """Every file pytest collects under tests/unit (pytest.ini: python_files = test_*.py)."""
    return sorted(
        p.relative_to(root).as_posix()
        for p in (root / UNIT_ROOT).rglob("test_*.py")
        if "__pycache__" not in p.parts
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    args_cmd = sub.add_parser("args", help="print the pytest arguments for a shard")
    args_cmd.add_argument("shard", choices=list(SHARDS))
    sub.add_parser("list", help="print the shard names")
    ns = parser.parse_args(argv)
    if ns.command == "list":
        print("\n".join(SHARDS))
    else:
        print("\n".join(pytest_args(ns.shard)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

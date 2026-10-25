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
`owns <shard> <test file>` prints true or false: whether that shard runs the file.

The run ends when the slowest shard ends. scripts/unit_shard_durations.json
holds measured minutes per test file, and `balance` prints each shard's total;
scripts/test_unit_shards.py fails when one exceeds MAX_OVER_MEAN times the
average. To refresh the minutes, download the py-junit-* artifacts of a green
unit-test run and run
  python3 scripts/unit_shards.py durations --source "<run>" junit/*/*.xml \
    > scripts/unit_shard_durations.json
To rebalance, move paths between SHARDS until `balance` is even.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parent.parent / "backend" / "python"
UNIT_ROOT = "tests/unit"
DURATIONS = Path(__file__).resolve().parent / "unit_shard_durations.json"

# How far the longest shard may sit above the average before the guard fails.
MAX_OVER_MEAN = 1.35

SHARDS: dict[str, tuple[str, ...]] = {
    # The connector test files directly under sources/.
    "connectors-a": ("tests/unit/connectors/sources/test_*.py",),
    # web, localKB and the smaller connector directories.
    "connectors-b": ("tests/unit/connectors/sources",),
    # google, microsoft, and connectors outside sources/.
    "connectors-c": (
        "tests/unit/connectors",
        "tests/unit/connectors/sources/google",
        "tests/unit/connectors/sources/microsoft",
    ),
    "agents": ("tests/unit/agents", "tests/unit/agent_loop_lib"),
    "services": (
        "tests/unit/services",
        "tests/unit/events",
        "tests/unit/sandbox",
        "tests/unit/containers",
        "tests/unit/config",
        "tests/unit/telemetry",
    ),
    "modules": ("tests/unit/modules", "tests/unit/utils"),
    # Top-level test files, api, sources, models and the rest.
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


def shard_owns(shard: str, test_file: str, root: Path = PYTHON_ROOT) -> bool:
    """Whether `shard` runs `test_file` (a path relative to backend/python)."""
    if shard not in SHARDS:
        raise KeyError(f"unknown shard {shard!r}; known: {', '.join(SHARDS)}")
    owners = owned_paths(root)
    return owners[nearest_owner(test_file, owners)] == shard


def test_files(root: Path = PYTHON_ROOT) -> list[str]:
    """Every file pytest collects under tests/unit (pytest.ini: python_files = test_*.py)."""
    return sorted(
        p.relative_to(root).as_posix()
        for p in (root / UNIT_ROOT).rglob("test_*.py")
        if "__pycache__" not in p.parts
    )


def _file_of(classname: str, files: set[str]) -> str | None:
    """The test file a JUnit classname (tests.unit.x.test_y.TestZ) comes from."""
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        candidate = "/".join(parts[:end]) + ".py"
        if candidate in files:
            return candidate
    return None


def file_minutes(junit_paths: list[Path], root: Path = PYTHON_ROOT) -> dict[str, float]:
    """Minutes of test time per test file, summed over every report."""
    files = set(test_files(root))
    seconds: dict[str, float] = {}
    for junit in junit_paths:
        for case in ET.parse(junit).iter("testcase"):
            path = _file_of(case.get("classname", ""), files)
            if path is not None:
                seconds[path] = seconds.get(path, 0.0) + float(case.get("time") or 0)
    return {path: round(sec / 60, 3) for path, sec in sorted(seconds.items())}


def load_durations(path: Path = DURATIONS) -> dict[str, float]:
    return json.loads(path.read_text(encoding="utf-8"))["files"]


def shard_minutes(durations: dict[str, float], root: Path = PYTHON_ROOT) -> dict[str, float]:
    """Measured minutes per shard. A file with no measurement counts as zero."""
    owners = owned_paths(root)
    totals = dict.fromkeys(SHARDS, 0.0)
    for test_file in test_files(root):
        totals[owners[nearest_owner(test_file, owners)]] += durations.get(test_file, 0.0)
    return totals


def balance_problems(totals: dict[str, float]) -> list[str]:
    mean = sum(totals.values()) / len(totals)
    limit = mean * MAX_OVER_MEAN
    return [
        f"{shard} has {minutes:.1f} min of tests, over {limit:.1f} "
        f"({MAX_OVER_MEAN} x the {mean:.1f} min average): move paths out of it in SHARDS"
        for shard, minutes in totals.items()
        if minutes > limit
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    args_cmd = sub.add_parser("args", help="print the pytest arguments for a shard")
    args_cmd.add_argument("shard", choices=list(SHARDS))
    owns_cmd = sub.add_parser("owns", help="print true if the shard runs the test file, else false")
    owns_cmd.add_argument("shard", choices=list(SHARDS))
    owns_cmd.add_argument("test_file", help="path relative to backend/python")
    sub.add_parser("list", help="print the shard names")
    durations_cmd = sub.add_parser("durations", help="per-file minutes from JUnit reports, as JSON")
    durations_cmd.add_argument("--source", required=True, help="where the reports came from")
    durations_cmd.add_argument("junit", nargs="+", type=Path)
    sub.add_parser("balance", help="print measured minutes per shard")
    ns = parser.parse_args(argv)
    if ns.command == "list":
        print("\n".join(SHARDS))
    elif ns.command == "owns":
        print("true" if shard_owns(ns.shard, ns.test_file) else "false")
    elif ns.command == "args":
        print("\n".join(pytest_args(ns.shard)))
    elif ns.command == "durations":
        print(json.dumps({
            "_source": ns.source,
            "_units": "Minutes of test time per file, summed over xdist workers; "
                      "a shard's wall clock is about its total / 4 plus setup and collection.",
            "files": file_minutes(ns.junit),
        }, indent=1))
    else:
        totals = shard_minutes(load_durations())
        for shard, minutes in sorted(totals.items(), key=lambda item: -item[1]):
            print(f"{shard:14} {minutes:6.1f} min")
        problems = balance_problems(totals)
        print("\n".join(problems) or "balanced")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

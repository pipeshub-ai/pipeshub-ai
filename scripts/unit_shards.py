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
average. To refresh the minutes, download the py-junit-* artifacts of a few
green unit-test runs, turn each into a durations file and keep each file's
median, so no shard's minutes come from one runner's speed:
  python3 scripts/unit_shards.py durations --source "<run>" junit-<run>/*/*.xml > <run>.json
  python3 scripts/unit_shards.py median --source "<runs>" <run>.json ... \
    > scripts/unit_shard_durations.json
To rebalance, move paths between SHARDS until `balance` is even. unit-test.yml
also runs `balance --durations` on each run's own timings, so a shard that
grows past the bound fails the run that grew it, before it reaches its job's
time limit.

Each shard runs on a runner of its own, and hosted runners differ in speed:
one run measured every services file at a median 1.48 times another's, with
the same tests. So `balance --durations` first reads how fast each shard's
runner was, as the median ratio of its files' minutes to the committed ones
(`runner_speeds`), and compares the shards' minutes at the committed speed. A
shard that grew still shows: new files have no committed minutes and count in
full, and files that slowed down move the median only when most of the shard
did.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parent.parent / "backend" / "python"
UNIT_ROOT = "tests/unit"
DURATIONS = Path(__file__).resolve().parent / "unit_shard_durations.json"

# How far the longest shard may sit above the average before the guard fails.
MAX_OVER_MEAN = 1.35

SHARDS: dict[str, tuple[str, ...]] = {
    # The connector test files directly under sources/, microsoft, and services/messaging (the record consumer's
    # replay and sweep suites), which held services over the balance bound.
    "connectors-a": (
        "tests/unit/connectors/sources/test_*.py",
        "tests/unit/connectors/sources/microsoft",
        "tests/unit/services/messaging",
    ),
    # web, localKB and the smaller connector directories, and the parser engine's and graph modules' suites.
    "connectors-b": (
        "tests/unit/connectors/sources",
        "tests/unit/modules/parse_engine",
        "tests/unit/modules/named_entities",
        "tests/unit/modules/code_graph",
        "tests/unit/modules/graph_view",
        "tests/unit/modules/retrieval",
    ),
    # google, connectors outside sources/, and the API routes.
    "connectors-c": (
        "tests/unit/connectors",
        "tests/unit/connectors/sources/google",
        "tests/unit/api",
    ),
    # agents, modules/transformers and modules/pipeline, and the paid-result cache.
    "agents": (
        "tests/unit/agents",
        "tests/unit/agent_loop_lib",
        "tests/unit/modules/transformers",
        "tests/unit/modules/pipeline",
        "tests/unit/services/paid_cache",
    ),
    # Keeps tests/unit/services/vector_db: unit-test.yml runs the pgvector suites again in this shard.
    "services": (
        "tests/unit/services",
        "tests/unit/events",
        "tests/unit/sandbox",
        "tests/unit/containers",
        "tests/unit/config",
        "tests/unit/utils",
    ),
    # Keeps tests/unit/modules/parsers: unit-test.yml installs LibreOffice for this shard. The resource governor's and
    # telemetry's suites keep services, the largest shard, under the bound.
    "modules": (
        "tests/unit/modules",
        "tests/unit/services/resource_governor",
        "tests/unit/telemetry",
    ),
    # The agent harness: its service tests and its engines.
    "harness": ("tests/unit/harness", "tests/unit/harness_engines"),
    # The harness kernel, its generated contracts and the control plane.
    "harness-contracts": (
        "tests/unit/harness_core",
        "tests/unit/contracts_harness",
        "tests/unit/control_plane",
    ),
    # Top-level test files, sources, models and the rest.
    "core": (UNIT_ROOT,),
}

# A marker expression a shard adds. The harness's `slow` modules are gate runs (quiet-host benchmarks, crash and
# fault matrices; tests/cohesion/test_c25_slow_lanes.py) that a shared per-PR runner cannot time; main's own `slow`
# modules run in its shards.
SHARD_MARKERS: dict[str, str] = {
    "harness": "not slow",
    "harness-contracts": "not slow",
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
    marker = ["-m", SHARD_MARKERS[shard]] if shard in SHARD_MARKERS else []
    return positional + sorted(ignores) + marker


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


# Files the reference measured under this many minutes are mostly fixture and import time, which runner speed
# barely moves; they would only blur the median.
MIN_SPEED_MINUTES = 0.05
# Below this many such files a shard's runner speed is not estimated, and its minutes are taken as measured.
MIN_SPEED_FILES = 20
# The furthest a runner's speed is credited from the reference's. The shards of the five runs behind the committed
# minutes read 0.76 to 1.23 against them; a shard twice as slow is more likely one whose every test got slower.
MAX_RUNNER_SPREAD = 2.0


def runner_speeds(
    durations: dict[str, float], reference: dict[str, float], root: Path = PYTHON_ROOT
) -> dict[str, float]:
    """How many times its reference minutes each shard's files took (its runner's speed), clamped to the spread."""
    owners = owned_paths(root)
    ratios: dict[str, list[float]] = {shard: [] for shard in SHARDS}
    for test_file in test_files(root):
        ref, now = reference.get(test_file, 0.0), durations.get(test_file, 0.0)
        if ref >= MIN_SPEED_MINUTES and now > 0:
            ratios[owners[nearest_owner(test_file, owners)]].append(now / ref)
    return {
        shard: (
            min(max(statistics.median(found), 1 / MAX_RUNNER_SPREAD), MAX_RUNNER_SPREAD)
            if len(found) >= MIN_SPEED_FILES else 1.0
        )
        for shard, found in ratios.items()
    }


def median_durations(runs: list[dict[str, float]]) -> dict[str, float]:
    """Each file's median minutes over the runs that measured it."""
    files = sorted({f for run in runs for f in run})
    return {f: round(statistics.median(run[f] for run in runs if f in run), 3) for f in files}


def balance_problems(totals: dict[str, float]) -> list[str]:
    mean = sum(totals.values()) / len(totals)
    limit = mean * MAX_OVER_MEAN
    return [
        f"{shard} has {minutes:.1f} min of tests, over {limit:.1f} "
        f"({MAX_OVER_MEAN} x the {mean:.1f} min average): move paths out of it in SHARDS"
        for shard, minutes in totals.items()
        if minutes > limit
    ]


# The share of a shard's test files that may have no measured minutes. Past it the
# balance check is reading zeros: 1,250 new test directories' files once counted
# as nothing while the shard holding them ran into the job's time limit. Files
# added since the last refresh stay well under it.
MAX_UNMEASURED_SHARE = 0.5


def unmeasured_problems(durations: dict[str, float], root: Path = PYTHON_ROOT) -> list[str]:
    owners = owned_paths(root)
    files = dict.fromkeys(SHARDS, 0)
    unmeasured = dict.fromkeys(SHARDS, 0)
    for test_file in test_files(root):
        shard = owners[nearest_owner(test_file, owners)]
        files[shard] += 1
        unmeasured[shard] += test_file not in durations
    return [
        f"{shard}: {unmeasured[shard]} of its {files[shard]} test files have no measured minutes: "
        "refresh unit_shard_durations.json (see the module docstring)"
        for shard in SHARDS
        if files[shard] and unmeasured[shard] > files[shard] * MAX_UNMEASURED_SHARE
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
    balance_cmd = sub.add_parser("balance", help="print measured minutes per shard")
    # unit-test.yml passes this run's own timings: the committed file is a snapshot, and main's merges once grew a
    # shard past its job's time limit while the snapshot still read balanced.
    balance_cmd.add_argument("--durations", type=Path, default=DURATIONS, help="a `durations` JSON file")
    balance_cmd.add_argument(
        "--reference", type=Path, default=DURATIONS, help="the minutes runner speeds are read against"
    )
    median_cmd = sub.add_parser("median", help="each file's median minutes over several `durations` files")
    median_cmd.add_argument("--source", required=True, help="where the runs came from")
    median_cmd.add_argument("runs", nargs="+", type=Path)
    ns = parser.parse_args(argv)
    if ns.command == "list":
        print("\n".join(SHARDS))
    elif ns.command == "owns":
        print("true" if shard_owns(ns.shard, ns.test_file) else "false")
    elif ns.command == "args":
        print("\n".join(pytest_args(ns.shard)))
    elif ns.command in ("durations", "median"):
        files = file_minutes(ns.junit) if ns.command == "durations" else median_durations(
            [load_durations(run) for run in ns.runs]
        )
        print(json.dumps({
            "_source": ns.source,
            "_units": "Minutes of test time per file, summed over xdist workers; "
                      "a shard's wall clock is about its total / 4 plus setup and collection.",
            "files": files,
        }, indent=1))
    else:
        durations = load_durations(ns.durations)
        measured = shard_minutes(durations)
        speeds = runner_speeds(durations, load_durations(ns.reference))
        totals = {shard: minutes / speeds[shard] for shard, minutes in measured.items()}
        for shard, minutes in sorted(totals.items(), key=lambda item: -item[1]):
            print(f"{shard:18} {minutes:6.1f} min  ({measured[shard]:.1f} measured, runner {speeds[shard]:.2f}x)")
        problems = unmeasured_problems(durations) + balance_problems(totals)
        print("\n".join(problems) or "balanced")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

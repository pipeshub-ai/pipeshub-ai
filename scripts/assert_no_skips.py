#!/usr/bin/env python3
"""Fail when a JUnit/xunit report (pytest --junitxml, mocha --reporter xunit) holds a skipped test that is not allow-listed.

A suite that "passed" with its store down had skipped every store-backed test; the workflows that start the services run this
after the tests. usage: assert_no_skips.py REPORT.xml [REPORT.xml ...] [--allow SUBSTRING ...] [--min-tests N] [--expect-suite 'TITLE=N' ...]
[--collected FILE ...]

--expect-suite: exactly N cases whose suite title (the xunit classname) starts with TITLE ran, and none of them failed or errored. A suite that
registered nothing (or fewer cases than it should) is not a pass.

--collected: exactly the pytest cases listed in FILE ran, no more and no fewer. FILE is written before the run, in the same step and with the
same arguments, by: assert_no_skips.py --collect FILE PYTEST_ARG ... It runs `pytest --collect-only` and fails on a collection error, on
nothing collected, and on a test file named in the arguments that holds no selected case. The count is never written down, so a pull
request that adds a test changes no pin.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def skipped(paths: list[str]) -> tuple[int, list[str]]:
    total, skips = 0, []
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            total += 1
            if case.find("skipped") is not None:
                skips.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return total, skips


def failed(paths: list[str]) -> list[str]:
    """One line per failed or errored case: the xunit reporter writes only to its file, so the log would not name it otherwise."""
    out = []
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            for bad in case.findall("failure") + case.findall("error"):
                msg = ((bad.get("message") or bad.text or "").strip().splitlines() or [""])[0]
                out.append(f"FAILED {case.get('classname', '')} :: {case.get('name', '')}: {msg}")
    return out


def suite_counts(paths: list[str], prefix: str) -> tuple[int, int]:
    """(cases whose classname starts with `prefix`, how many of those failed or errored)."""
    n = bad = 0
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            if (case.get("classname") or "").startswith(prefix):
                n += 1
                bad += case.find("failure") is not None or case.find("error") is not None
    return n, bad


def junit_id(nodeid: str) -> str:
    """The `classname::name` pytest's junit report gives the case with this node id (_pytest.junitxml.mangle_test_address)."""
    path, bracket, params = nodeid.partition("[")
    names = path.split("::")
    names[0] = re.sub(r"\.py$", "", names[0].replace("/", "."))
    return f"{'.'.join(names[:-1])}::{names[-1]}{bracket}{params}"


def collect(args: list[str], cwd: str | Path | None = None) -> tuple[list[str], str]:
    """(the node ids `pytest --collect-only ARGS` selects, the reason they cannot be trusted or '')."""
    # --verbosity=-1 after pytest.ini's `addopts = -q`: one node id per line (at -2 pytest prints a count per file instead)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *args, "--collect-only", "--verbosity=-1"], check=False, cwd=cwd, capture_output=True, text=True
    )
    ids = [line for line in proc.stdout.split("\n\n", 1)[0].splitlines() if "::" in line]
    if proc.returncode or not ids:
        # an import error takes its whole file out of the run; pytest exits 2 for it and 5 for nothing collected
        return ids, f"pytest --collect-only exited {proc.returncode} with {len(ids)} cases:\n{proc.stderr}{proc.stdout}"
    files = [a for prev, a in zip(["", *args], args) if a.endswith(".py") and not a.startswith("-") and prev not in ("--ignore", "--deselect")]
    # node ids are relative to pytest's rootdir, which the output does not name: match a file by the tail of its resolved path.
    # shortcut: a same-named file at a shorter path could stand in for an empty one; read rootdir from pytest if that layout appears.
    base = Path(cwd or ".").resolve()
    selected = {Path(i.split("::", 1)[0]).parts for i in ids}
    empty = [f for f in files if not any((base / f).resolve().parts[-len(s):] == s for s in selected)]
    return ids, "".join(f"{f}: no case selected (a suite that registers nothing is not a pass)\n" for f in empty)


def shard_problems(folder: list[str], shards: dict[object, list[str]]) -> list[str]:
    """Every case collected from the folder runs in exactly one shard, and the shards run nothing else."""
    runs = Counter(i for ids in shards.values() for i in ids)
    whole = set(folder)
    return (
        [f"in no shard: {i}" for i in folder if i not in runs]
        + [f"in {n} shards: {i}" for i, n in runs.items() if n > 1]
        + [f"not in the folder: {i}" for i in runs if i not in whole]
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--collect"]:
        if len(argv) < 3:
            sys.exit("usage: assert_no_skips.py --collect FILE PYTEST_ARG ...")
        ids, problem = collect(argv[2:])
        sys.stdout.write(f"{problem or f'{len(ids)} cases collected'}\n")
        if problem:
            return 1
        Path(argv[1]).write_text("".join(f"{i}\n" for i in ids))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--allow", action="append", default=[], help="substring of a skipped test id that may be skipped")
    ap.add_argument("--min-tests", type=int, default=1, help="fail when fewer tests than this ran (an empty run is not a pass)")
    ap.add_argument("--expect-suite", action="append", default=[], metavar="TITLE=N", help="exactly N passing cases in suites titled TITLE...")
    ap.add_argument("--collected", action="append", default=[], metavar="FILE", help="node ids written by --collect: exactly these cases ran")
    args = ap.parse_args(argv)
    suite_failed = False
    for spec in args.expect_suite:
        title, _, want = spec.rpartition("=")
        n, bad = suite_counts(args.reports, title)
        print(f"suite '{title}': {n} cases (expected {want}), {bad} failed")
        # A count of 0 would pass a suite that never ran; N must be at least 1.
        if not title or not want.isdigit() or int(want) < 1 or n != int(want) or bad:
            suite_failed = True
    if args.collected:
        want = {junit_id(i) for path in args.collected for i in Path(path).read_text().splitlines() if i}
        ran = {f"{c.get('classname', '')}::{c.get('name', '')}" for path in args.reports for c in ET.parse(path).getroot().iter("testcase")}
        sys.stdout.write(
            f"{len(want)} cases collected, {len(want - ran)} of them did not run, {len(ran - want)} ran that were not collected\n"
            + "".join(f"NOT RUN {i}\n" for i in sorted(want - ran))
            + "".join(f"NOT COLLECTED {i}\n" for i in sorted(ran - want))
        )
        suite_failed |= want != ran
    fails = failed(args.reports)
    print("\n".join(fails))
    total, skips = skipped(args.reports)
    bad = [s for s in skips if not any(a in s for a in args.allow)]
    print(f"{total} tests, {len(skips)} skipped, {len(skips) - len(bad)} allow-listed")
    for s in bad:
        print(f"UNEXPECTED SKIP {s}")
    if total < args.min_tests:
        print(f"only {total} tests ran, expected at least {args.min_tests}")
        return 1
    return 1 if bad or suite_failed or fails else 0


if __name__ == "__main__":
    sys.exit(main())

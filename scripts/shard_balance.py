"""Check the integration-test shards: every connector runs once, and the shards stay balanced.

The nightly run splits connector suites across CONN_SHARD_1..N in
`.github/workflows/integration-tests.yml`; the `core` shard is everything those
do not name. Connector tests are also marked `integration`, so a connector left
out of every shard is not skipped — it falls into `core`, quietly making that
shard longer, which is the imbalance the split exists to avoid. A CONN_SHARD_N
with no matching `connectors-N` job is worse: `core` excludes it and no job
selects it, so those tests really do stop running. A shard that grows far past
the others decides how long the whole nightly takes.

Run: python3 scripts/shard_balance.py --check
Rebalance: move markers between the CONN_SHARD_* lines until this reports even
shards, using the measured minutes in scripts/shard_durations.json. Refresh
those minutes from a recent nightly's reports-both-<shard> artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/integration-tests.yml"
PYTEST_INI = REPO / "integration-tests/pytest.ini"
DURATIONS = REPO / "scripts/shard_durations.json"

# How far the longest shard may sit above the average before this fails. The
# nightly ends when the slowest shard ends, so drift here is wasted wall clock.
MAX_OVER_MEAN = 1.35

_SHARD_LINE = re.compile(r'^\s*CONN_SHARD_(\d+):\s*"([^"]*)"\s*$', re.MULTILINE)
_MARKER_LINE = re.compile(r"^\s{4}(\w+):\s*(.+)$")
_MATRIX_LINE = re.compile(r"^\s*shard:\s.*$", re.MULTILINE)
_MATRIX_SHARD = re.compile(r'"(connectors-\d+)"')


def matrix_shards(workflow_text: str) -> set[str]:
    """The connector shard jobs the matrix actually runs."""
    line = _MATRIX_LINE.search(workflow_text)
    return set(_MATRIX_SHARD.findall(line.group(0))) if line else set()


def shard_markers(workflow_text: str) -> dict[str, list[str]]:
    """Marker names per CONN_SHARD_*, in the order the workflow lists them."""
    shards: dict[str, list[str]] = {}
    for number, expression in _SHARD_LINE.findall(workflow_text):
        shards[f"connectors-{number}"] = [
            part.strip() for part in expression.split(" or ") if part.strip()
        ]
    return shards


def declared_markers(pytest_ini_text: str) -> dict[str, str]:
    """Every marker declared in pytest.ini, mapped to its description."""
    markers: dict[str, str] = {}
    inside = False
    for line in pytest_ini_text.splitlines():
        if line.startswith("markers"):
            inside = True
            continue
        if inside:
            if line.strip().startswith("#"):
                continue
            match = _MARKER_LINE.match(line)
            if not match:
                if line.strip() and not line.startswith(" "):
                    break
                continue
            markers[match.group(1)] = match.group(2).strip()
    return markers


def connector_markers(markers: dict[str, str]) -> set[str]:
    """Markers for a single connector's suite, which must live in exactly one shard.

    Picked out by their description: every connector marker in pytest.ini says
    so ("marks tests specific to the X connector"). A suite that is not tied to
    one connector (cleanup, retrieval, oauth_*, ...) belongs to `core`, which
    takes whatever the shards do not name.
    """
    return {
        name
        for name, description in markers.items()
        if "connector" in description.lower()
    }


def check(
    workflow_text: str,
    pytest_ini_text: str,
    durations: dict[str, float],
    core_minutes: float | None = None,
) -> tuple[list[str], list[str]]:
    """Return (problems, report lines). Problems empty means the split is sound."""
    problems: list[str] = []
    report: list[str] = []

    shards = shard_markers(workflow_text)
    if not shards:
        return (["No CONN_SHARD_* lines found in the workflow."], report)

    markers = declared_markers(pytest_ini_text)
    connectors = connector_markers(markers)

    jobs = matrix_shards(workflow_text)
    for shard in sorted(set(shards) - jobs):
        number = shard.rsplit("-", 1)[-1]
        problems.append(
            f"CONN_SHARD_{number} lists suites but the matrix has no '{shard}' job, so "
            f"nothing selects them and `core` excludes them: those tests stop running. "
            f"Add '{shard}' to the shard matrix in {WORKFLOW.name}."
        )
    for shard in sorted(jobs - set(shards)):
        problems.append(
            f"The matrix runs a '{shard}' job with no matching CONN_SHARD_* line, so it "
            f"would run with an empty marker expression."
        )

    seen: dict[str, str] = {}
    for shard, names in sorted(shards.items()):
        for name in names:
            if name not in markers:
                problems.append(
                    f"{shard} names '{name}', which is not a marker in pytest.ini "
                    f"(a rename? nothing would select those tests)."
                )
            elif name not in connectors:
                problems.append(
                    f"{shard} names '{name}', which is not a single connector's marker. "
                    f"A shard must list only connector suites: '{name}' would pull in "
                    f"tests that `core` then excludes, skewing both."
                )
            if name in seen:
                problems.append(
                    f"'{name}' is in both {seen[name]} and {shard}; it would run twice."
                )
            seen[name] = shard

    for name in sorted(connectors - set(seen)):
        problems.append(
            f"Connector marker '{name}' is in no shard. Its tests are marked "
            f"`integration`, so they fall into `core` instead of their own shard, making "
            f"`core` slower and the split uneven. Add it to a CONN_SHARD_* line in "
            f"{WORKFLOW.name}."
        )

    unmeasured = sorted(set(seen) - set(durations))
    totals = {
        shard: sum(durations.get(name, 0.0) for name in names)
        for shard, names in sorted(shards.items())
    }
    mean = sum(totals.values()) / len(totals)

    report.append(f"{len(seen)} connector suites across {len(shards)} shards, "
                  f"{sum(totals.values()):.0f} min total, {mean:.0f} min average:")
    for shard, total in sorted(totals.items(), key=lambda item: -item[1]):
        share = total / mean if mean else 1.0
        report.append(f"  {shard:14s} {total:6.1f} min  ({share:.2f}x average)  "
                      f"{len(shards[shard])} suites")
    if core_minutes is not None:
        report.append(f"  {'core':14s} {core_minutes:6.1f} min  (for reference; not marker-split)")
    if unmeasured:
        report.append(f"  no measured time yet (counted as 0): {', '.join(unmeasured)}")

    if mean:
        worst, worst_total = max(totals.items(), key=lambda item: item[1])
        if worst_total / mean > MAX_OVER_MEAN:
            problems.append(
                f"{worst} is {worst_total / mean:.2f}x the average shard "
                f"({worst_total:.0f} min vs {mean:.0f} min). The nightly ends when the "
                f"slowest shard ends, so move suites off it, using the minutes in "
                f"{DURATIONS.name}."
            )
    return (problems, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when a connector is unassigned or the shards drift apart")
    args = parser.parse_args()

    measured = json.loads(DURATIONS.read_text(encoding="utf-8"))
    problems, report = check(
        WORKFLOW.read_text(encoding="utf-8"),
        PYTEST_INI.read_text(encoding="utf-8"),
        measured["minutes"],
        measured.get("_core_minutes"),
    )
    print("\n".join(report))
    for problem in problems:
        print(f"\nPROBLEM: {problem}")
    if problems and args.check:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

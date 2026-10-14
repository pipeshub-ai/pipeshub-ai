"""Which connector suites actually ran, and which covered nothing.

A suite whose every test skipped reports no failures, so a run can look green
while a connector was never exercised. This reads the run's JUnit files and
says so in the job summary.

Run: python report_connector_coverage.py reports/neo4j-results.xml [...]
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def suite_name(classname: str) -> str | None:
    """The connector a test belongs to, from its dotted class name."""
    parts = classname.split(".")
    if len(parts) < 2 or parts[0] != "connectors":
        return None
    return parts[1]


def tally(paths: list[Path]) -> dict[str, tuple[int, int]]:
    """{connector: (tests, skipped)} across every JUnit file given."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for path in paths:
        if not path.is_file():
            continue
        for case in ET.parse(path).getroot().iter("testcase"):
            name = suite_name(case.get("classname", ""))
            if name is None:
                continue
            counts[name][0] += 1
            if case.find("skipped") is not None:
                counts[name][1] += 1
    return {name: (total, skipped) for name, (total, skipped) in counts.items()}


def report(counts: dict[str, tuple[int, int]]) -> str:
    """A short plain-words summary for the job page."""
    if not counts:
        return "No connector tests were collected in this run."

    covered = sorted(n for n, (t, s) in counts.items() if s < t)
    empty = sorted(n for n, (t, s) in counts.items() if s == t)

    lines = [
        "### Connector coverage in this run",
        "",
        f"{len(covered)} connector suites ran; {len(empty)} covered nothing.",
    ]
    if empty:
        lines += [
            "",
            "Every test skipped in: " + ", ".join(empty) + ".",
            "",
            "A suite skips when its credentials are missing. On the nightly that is "
            "a failure instead — if you are seeing this on a scheduled run, the "
            "suite was not in the shard that ran.",
        ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    print(report(tally([Path(arg) for arg in argv[1:]])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

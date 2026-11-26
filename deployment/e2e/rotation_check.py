"""KEYRING-FU-1: whether a ``run.sh keyring`` rotation moved everything, read from the CLI's result lines.

    python3 rotation_check.py <rotate-secret-key.log>

The log holds one result line per CLI command, in the runbook's order (dry-run, rotate, verify, retire, verify).
A whole rotation: every command ok; the dry run counts something under the previous key in each step that holds data;
rotate moves it, the org data key included; the last verify, on the new key alone, finds nothing previous or
unreadable. Prints one line per finding, counts only, and exits non-zero on any.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ORDER = ["dry-run", "rotate", "verify", "retire", "verify"]


def results(log: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for line in log.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            record = json.loads(line[start:])
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("event") == "result":
            found.append(record)
    return found


def findings(found: list[dict[str, object]]) -> list[str]:
    modes = [str(r["mode"]) for r in found]
    if modes != ORDER:
        return [f"the commands ran as {modes}, not {ORDER}"]
    problems = [f"{r['mode']} was not ok (refused: {r.get('refused')})" for r in found if r.get("ok") is not True]
    steps = {str(r["mode"]) + str(i): {str(s["step"]): s for s in r["steps"]} for i, r in enumerate(found)}  # type: ignore[union-attr]
    rotate, last = steps["rotate1"], steps["verify4"]
    if int(rotate["data-keys"]["moved"]) < 1:
        problems.append("rotate moved no org data key: the turn before the rotation made none")
    for name, step in rotate.items():
        before = steps["dry-run0"][name]
        if int(step["moved"]) != int(before["previous"]):
            problems.append(f"{name}: the dry run counted {before['previous']} under the previous key, rotate moved {step['moved']}")
    for name, step in last.items():
        if int(step["previous"]) or int(step["unreadable"]):
            problems.append(f"{name}: {step['previous']} still under the previous key, {step['unreadable']} unreadable")
    return problems


def main() -> int:
    problems = findings(results(Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")))
    for problem in problems:
        print(f"rotation: {problem}")  # noqa: T201 - the check's output
    if not problems:
        print("rotation: whole (every command ok, everything moved, nothing left under the previous key)")  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

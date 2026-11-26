"""One result line per row (04 §4.5, QA-6 item 4): ``<row> <stack> <result> [<finding or first problem>]``.

``pass``, ``fail``, ``red <finding>`` (a strict xfail that failed as recorded), ``green-but-recorded-red <finding>``
(it passed: the known-red entry must go), ``blocked <finding>`` (the lane cannot set the row up yet) and ``error``
(setup failed). The lines are sorted by row id so two runs diff cleanly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matrix import Row

FINDING = re.compile(r"\b(QA6-F\d+|[A-Z0-9]+-G\d+|QA\d+-F\d+)\b")


@dataclass(frozen=True)
class RowResult:
    row: str
    stack: str
    result: str
    detail: str

    def line(self) -> str:
        return f"{self.row} {self.stack} {self.result}" + (f" {self.detail}" if self.detail else "")


def _first(text: str, limit: int = 160) -> str:
    one = " ".join(text.split())
    return one[:limit]


def _problem(longrepr: str) -> str:
    """The first problem a failed row names (its assertion lists them one per line), else the report's last line."""
    lines = [line[1:].strip() for line in longrepr.splitlines() if line.startswith("E ")]
    named = [line for line in lines if line.startswith(("oracle", "[", "history"))]
    return _first(named[0] if named else (lines[0] if lines else (longrepr.strip().splitlines() or [""])[-1]))


def classify(when: str, outcome: str, wasxfail: str | None, longrepr: str) -> tuple[str, str] | None:
    """The row's result from one pytest report, or None when the report says nothing new (a passed setup)."""
    finding = FINDING.search(wasxfail or longrepr or "")
    found = finding.group(1) if finding else ""
    if when == "setup" and outcome == "skipped":
        return "blocked", _first(longrepr.split("Skipped: ", 1)[-1]) if longrepr else found
    if when == "call" and outcome == "skipped" and wasxfail is not None:
        return "red", found
    if when == "call" and outcome == "skipped":
        return "blocked", _first(longrepr.split("Skipped: ", 1)[-1])
    if when == "call" and outcome == "passed":
        return ("green-but-recorded-red", found) if wasxfail is not None else ("pass", "")
    if outcome == "failed":
        return ("fail" if when == "call" else "error"), _problem(longrepr)
    return None


class ResultLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.results: dict[str, RowResult] = {}

    def record(self, row: Row, report: Any) -> None:  # noqa: ANN401 - a pytest TestReport
        longrepr = str(report.longrepr) if report.longrepr is not None else ""
        if isinstance(report.longrepr, tuple):
            longrepr = str(report.longrepr[2])
        verdict = classify(report.when, report.outcome, getattr(report, "wasxfail", None), longrepr)
        if verdict is not None and (row.id not in self.results or self.results[row.id].result in ("pass",)):
            self.results[row.id] = RowResult(row.id, row.stack, *verdict)

    def lines(self) -> list[str]:
        return [self.results[k].line() for k in sorted(self.results)]

    def write(self, default: Path) -> Path:
        target = self.path or default
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(self.lines()) + ("\n" if self.results else ""))
        return target

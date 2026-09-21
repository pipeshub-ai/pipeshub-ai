"""Backend warnings/errors during a run, grouped into message templates and
tied to the questions in flight when they were logged.

The agent often recovers from a backend failure (a tool error, a retried
call, a closed connection) and still answers, so these never surface in the
scores; recurring templates are a direct list of PipesHub bugs to look at.
Log lines carry no conversation id, so a line is attributed to every question
whose ask window contains it (`concurrent` > 1 means ambiguous).
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from benchmarks.frames.models import Prediction

_ISSUE = re.compile(r"\b(WARNING|ERROR|CRITICAL)\b|Traceback|Exception|is not running")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_VOLATILE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T][\d:.,]+Z?"), "<ts>"),
    (re.compile(r"\[req:[^\]]*\]"), ""),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{16,}\b"), "<hex>"),
    (re.compile(r"'[^']{0,200}'|\"[^\"]{0,200}\""), "<str>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
]
_SLACK = timedelta(seconds=5)


@dataclass
class LogIssue:
    template: str
    count: int = 0
    example: str = ""
    questions: set[tuple[str, int, int]] = field(default_factory=set)
    max_concurrent: int = 0


def template_of(message: str) -> str:
    text = _ANSI.sub("", message)
    for pattern, repl in _VOLATILE:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()[:240]


def read_docker_logs(container: str, since: datetime, until: datetime) -> list[tuple[datetime, str]]:
    out = subprocess.run(
        ["docker", "logs", "--timestamps", "--since", since.isoformat(), "--until", until.isoformat(), container],
        capture_output=True, text=True, check=True,
    )
    lines = []
    for raw in (out.stdout + out.stderr).splitlines():
        stamp, _, message = raw.partition(" ")
        try:
            at = datetime.fromisoformat(stamp[:26] + "+00:00" if stamp.endswith("Z") else stamp)
        except ValueError:
            continue
        lines.append((at, message))
    return sorted(lines)


def windows(predictions: Iterable[Prediction]) -> list[tuple[datetime, datetime, tuple[str, int, int]]]:
    return [
        (p.started_at - _SLACK, p.started_at + timedelta(milliseconds=p.latency_ms) + _SLACK, p.key)
        for p in predictions if p.started_at is not None
    ]


def group_issues(lines: Sequence[tuple[datetime, str]], spans: Sequence[tuple[datetime, datetime, tuple[str, int, int]]]) -> list[LogIssue]:
    issues: dict[str, LogIssue] = {}
    for at, message in lines:
        if not _ISSUE.search(message):
            continue
        key = template_of(message)
        issue = issues.setdefault(key, LogIssue(template=key, example=_ANSI.sub("", message)[:400]))
        issue.count += 1
        in_flight = {q for start, end, q in spans if start <= at <= end}
        issue.questions |= in_flight
        issue.max_concurrent = max(issue.max_concurrent, len(in_flight))
    return sorted(issues.values(), key=lambda i: (-len(i.questions), -i.count))


def render_issues(issues: Sequence[LogIssue], limit: int = 60) -> str:
    lines = [
        "## Backend warnings and errors during the run", "",
        "Grouped by message template (ids, numbers and strings masked). `Questions` counts the "
        "asks in flight when the line was logged.", "",
        "| # | Count | Questions | Systems | Template | Example |", "|---|---|---|---|---|---|",
    ]
    for n, issue in enumerate(issues[:limit], start=1):
        systems = ", ".join(sorted({q[0] for q in issue.questions})) or "–"
        esc = lambda s: s.replace("|", "\\|")  # noqa: E731
        lines.append(f"| {n} | {issue.count} | {len(issue.questions)} | {systems} | `{esc(issue.template)}` | {esc(issue.example[:160])} |")
    return "\n".join(lines) + "\n"


def collect(predictions: Sequence[Prediction], container: str) -> tuple[list[LogIssue], dict[str, list[int]]]:
    spans = windows(predictions)
    if not spans:
        return [], {}
    since = min(s for s, _e, _q in spans) - timedelta(minutes=1)
    until = max(e for _s, e, _q in spans) + timedelta(minutes=1)
    issues = group_issues(read_docker_logs(container, since.astimezone(UTC), until.astimezone(UTC)), spans)
    per_question: dict[str, list[int]] = defaultdict(list)
    for n, issue in enumerate(issues):
        for system, qid, repeat in issue.questions:
            per_question[f"{system}:{qid}:{repeat}"].append(n)
    return issues, dict(per_question)

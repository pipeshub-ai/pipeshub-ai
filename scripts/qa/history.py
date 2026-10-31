"""Run history: a directory of small flat files. Feeds "failing on main" and flaky
rates back into selection, and is what miss attribution and `qa stuck` read.

runs.jsonl       one compact entry per run (counts, non-pass outcomes only)
tests.json       {id: {"o": last <=10 outcome letters, newest last, "seen": "YYYY-MM-DD"}}
quarantine.json  {"tests": [entry...]}
shadow.jsonl     one shadow comparison per failed workflow run
metrics.json     overwritten on every publish
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

FILES = ("runs.jsonl", "tests.json", "quarantine.json", "shadow.jsonl", "metrics.json")
WINDOW = 10
KEEP_DAYS = 90
MAX_RUNS = 1000
COOLDOWN_DAYS = 30
LETTERS = {"pass": "p", "fail": "f", "error": "e", "flaky": "k"}
TRIGGERS = {"schedule": "nightly", "push": "main", "workflow_dispatch": "dispatch", "pull_request": "pr"}


def read_dir(path: Path) -> dict[str, str]:
    return {name: (path / name).read_text(encoding="utf-8") for name in FILES if (path / name).is_file()}


def write_dir(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    umask = os.umask(0)
    os.umask(umask)
    for name, text in files.items():
        # A crash mid-write must not leave a truncated file: write aside, then rename over it.
        fd, tmp = tempfile.mkstemp(dir=path, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, 0o666 & ~umask)  # mkstemp makes 0600; keep the mode a plain write would give
            os.replace(tmp, path / name)
        except BaseException:
            os.unlink(tmp)
            raise


def trigger(env: Mapping[str, str]) -> str:
    event = env.get("GITHUB_EVENT_NAME")
    return TRIGGERS.get(event, event) if event else "local"


def entry(plan: dict[str, Any], results: dict[str, Any], *, trigger: str, wall_seconds: float | None = None,
          run_url: str | None = None, ts: int | None = None) -> dict[str, Any]:
    if wall_seconds is None:
        parts = [i["seconds"] for i in results.get("invocations", []) if i.get("seconds") is not None]
        wall_seconds = max(parts) if parts else results.get("seconds")
    tests = results.get("tests", [])
    return {
        "ts": int(time.time()) if ts is None else ts,
        "head": plan.get("head"),
        "base": plan.get("base"),
        "branch": plan.get("branch"),
        "mode": plan.get("mode"),
        "profile": plan.get("profile"),
        "edition": plan.get("edition"),
        "planner": {k: plan.get("planner", {}).get(k) for k in ("adapter", "status", "model")},
        "trigger": trigger,
        "run_url": run_url,
        "wall_seconds": wall_seconds,
        # A full plan lists every test; storing them all would add megabytes per run.
        "selected": "all" if plan.get("profile") == "full" else {t["id"]: t["source"] for t in plan.get("tests", [])},
        "counts": dict(Counter(t["outcome"] for t in tests)),
        "outcomes": {t["id"]: t["outcome"] for t in tests if t["outcome"] != "pass"},
    }


def _jsonl(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _dump_jsonl(items: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(i, sort_keys=True) + "\n" for i in items)


def runs(files: Mapping[str, str]) -> list[dict[str, Any]]:
    return _jsonl(files.get("runs.jsonl", ""))


def _tests(files: Mapping[str, str]) -> dict[str, dict[str, str]]:
    try:
        return json.loads(files.get("tests.json") or "{}")
    except json.JSONDecodeError:
        return {}


def quarantine_entries(files: Mapping[str, str]) -> list[dict[str, Any]]:
    try:
        return list(json.loads(files.get("quarantine.json") or "{}").get("tests", []))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []


def tests_seen(files: Mapping[str, str], since: str = "") -> dict[str, dict[str, str]]:
    """tests.json entries last seen on or after `since` (YYYY-MM-DD)."""
    return {tid: w for tid, w in _tests(files).items() if w.get("seen", "") >= since}


def windows(files: Mapping[str, str]) -> dict[str, str]:
    return {tid: w.get("o", "") for tid, w in _tests(files).items()}


def add_run(files: dict[str, str], entry: dict[str, Any], results: dict[str, Any], today: dt.date) -> dict[str, str]:
    # Re-dump rather than append: a truncated last line would swallow the new entry.
    files = {**files, "runs.jsonl": _dump_jsonl(runs(files) + [entry])}
    tests = _tests(files)
    for t in results.get("tests", []):
        letter = LETTERS.get(t["outcome"])
        if letter:
            tests[t["id"]] = {"o": (tests.get(t["id"], {}).get("o", "") + letter)[-WINDOW:], "seen": today.isoformat()}
    files["tests.json"] = json.dumps(tests, sort_keys=True)
    return trim(files, today)


def trim(files: dict[str, str], today: dt.date) -> dict[str, str]:
    files = dict(files)
    cutoff_day = today - dt.timedelta(days=KEEP_DAYS)
    cutoff = dt.datetime.combine(cutoff_day, dt.time.min, tzinfo=dt.timezone.utc).timestamp()
    if "runs.jsonl" in files:
        files["runs.jsonl"] = _dump_jsonl([r for r in runs(files) if r.get("ts", 0) >= cutoff][-MAX_RUNS:])
    if "shadow.jsonl" in files:
        files["shadow.jsonl"] = _dump_jsonl([r for r in _jsonl(files["shadow.jsonl"]) if r.get("ts", 0) >= cutoff])
    if "tests.json" in files:
        files["tests.json"] = json.dumps(tests_seen(files, cutoff_day.isoformat()), sort_keys=True)
    if "quarantine.json" in files:
        gone = (today - dt.timedelta(days=COOLDOWN_DAYS)).isoformat()
        # An expired auto entry stays as a cooldown so the same id is not re-added at once.
        q = [e for e in quarantine_entries(files) if not (e.get("auto") and str(e.get("expires", "")) < gone)]
        files["quarantine.json"] = json.dumps({"tests": q}, sort_keys=True)
    return files


def failing_on_main(entries: Iterable[dict[str, Any]], branch: str = "main", last: int = 3) -> list[str]:
    runs = [e for e in entries if e.get("branch") == branch][-last:]
    return sorted({tid for e in runs for tid, o in e.get("outcomes", {}).items() if o in ("fail", "error")})


def flaky_rates(windows: dict[str, str]) -> dict[str, float]:
    return {tid: round(w.count("k") / len(w), 3) for tid, w in windows.items() if "k" in w}


def misses(entries: list[dict[str, Any]], failing: Iterable[str], culprit_head: str) -> list[str]:
    """Failing tests the culprit run did not select: selection misses."""
    run = next((e for e in reversed(entries) if e.get("head") == culprit_head), None)
    if run is None or run.get("selected") == "all":
        return []
    return sorted(t for t in failing if t not in run.get("selected", {}))


def stuck(runs: list[tuple[str, dict[str, Any]]], cycles: int = 3) -> list[str]:
    """Ids that failed in each of the last `cycles` runs with distinct heads (newest run per head)."""
    latest: list[dict[str, Any]] = []
    heads: set[str] = set()
    for head, results in reversed(runs):
        if head not in heads:
            heads.add(head)
            latest.append(results)
    if len(latest) < cycles:
        return []
    failing = [{t["id"] for t in r.get("tests", []) if t["outcome"] in ("fail", "error")} for r in latest[:cycles]]
    return sorted(set.intersection(*failing))

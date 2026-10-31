"""metrics.json: coverage, trend, flake rate, durations, caught vs escaped, and the enforcement verdict.

`compute` is pure: it reads the history files and catalog rows it is given. `bugs` and
`line_coverage` read GitHub and coverage reports; their results are passed in.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from qa import history, paths, quarantine, shadow

WINDOW_DAYS = 30
OUTCOMES = ("pass", "fail", "error", "flaky", "not_run")
# The PR workflows whose failure-then-success on a branch counts as a caught bug.
PR_WORKFLOWS = tuple(sorted(p.name for p in (paths.REPO / ".github/workflows").glob("qa-pr*.yml")))


def percentile(values: list[float], p: float) -> float | None:
    """Nearest rank: the smallest value with at least p% of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def line_coverage(path: Path) -> float | None:
    """Percent of lines covered from coverage.py JSON, Cobertura XML or istanbul coverage-summary.json."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        if text.lstrip().startswith("<"):
            return round(float(ET.fromstring(text).get("line-rate")) * 100, 2)
        doc = json.loads(text)
        pct = doc["totals"]["percent_covered"] if "totals" in doc else doc["total"]["lines"]["pct"]
        return round(float(pct), 2)
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError):
        return None


def _date(stamp: str) -> dt.date:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()


def bugs(repo: str, since: dt.date) -> dict[str, Any]:
    """Merged PRs the PR gate caught (a failure before the last success) and escaped issues, since `since`."""
    if not repo:
        return {"error": "no --repo given"}
    if not PR_WORKFLOWS:
        return {"error": "no qa-pr*.yml workflow found"}
    gh = shadow._gh  # looked up per call, so tests patch shadow._gh
    try:
        # shortcut: the newest 1000 of each listing; page with gh api --paginate if a window ever holds more.
        merged = [p for p in json.loads(gh(["pr", "list", "-R", repo, "--state", "merged", "--limit", "1000",
                                             "--json", "number,headRefName,mergedAt"]) or "[]")
                  if p.get("mergedAt") and _date(p["mergedAt"]) >= since]
        caught_branches: set[str] = set()
        for wf in PR_WORKFLOWS:
            by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in json.loads(gh(["run", "list", "-R", repo, "--workflow", wf, "--limit", "1000",
                                     "--json", "headBranch,conclusion,createdAt"]) or "[]"):
                by_branch[r.get("headBranch")].append(r)
            for branch, runs_ in by_branch.items():
                # shortcut: matched by branch name, so a reused branch name can borrow an older PR's runs.
                order = [r.get("conclusion") for r in sorted(runs_, key=lambda r: r.get("createdAt", ""))]
                if "success" in order and "failure" in order[:len(order) - order[::-1].index("success")]:
                    caught_branches.add(branch)
        issues = [i for i in json.loads(gh(["issue", "list", "-R", repo, "--label", "escaped", "--state", "all",
                                             "--limit", "1000", "--json", "number,createdAt,labels"]) or "[]")
                  if i.get("createdAt") and _date(i["createdAt"]) >= since]
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as err:
        return {"error": str(err) or type(err).__name__}
    by_flow: Counter[str] = Counter()
    unlinked = 0
    for i in issues:
        # A set: an issue counts once per flow, however often a label repeats.
        flows = {lbl["name"].split(":", 1)[1] for lbl in i.get("labels") or [] if (lbl.get("name") or "").startswith("flow:")}
        by_flow.update(flows)
        unlinked += not flows
    return {"caught": sum(p.get("headRefName") in caught_branches for p in merged), "escaped": len(issues),
            "escaped_by_flow": dict(sorted(by_flow.items())), "unlinked": unlinked}


def pr_walls(repo: str, since: dt.date) -> list[float]:
    """Wall seconds of completed PR workflow runs since `since`; [] on a gh error."""
    gh = shadow._gh
    out = []
    try:
        for wf in PR_WORKFLOWS:
            # shortcut: updatedAt stands in for the end time, and only the newest 1000 runs are read.
            for r in json.loads(gh(["run", "list", "-R", repo, "--workflow", wf, "--status", "completed", "--limit", "1000",
                                    "--json", "startedAt,updatedAt"]) or "[]"):
                if r.get("startedAt") and r.get("updatedAt") and _date(r["startedAt"]) >= since:
                    start, end = (dt.datetime.fromisoformat(r[k].replace("Z", "+00:00")) for k in ("startedAt", "updatedAt"))
                    out.append((end - start).total_seconds())
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as err:
        print(f"warning: no PR run durations: {err}", file=sys.stderr)
        return []
    return out


def _coverage(rows: list[dict[str, Any]], cfg: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Tests (catalog ids) per module and per flow. With the registry, untested ones show as 0."""
    modules: dict[str, dict[str, Any]] = {m["id"]: {"tests": 0} for m in (cfg.modules if cfg else [])}
    flows: dict[str, dict[str, Any]] = {f["id"]: {"tests": 0, "priority": f.get("priority")} for f in (cfg.flows if cfg else [])}
    registered = set(flows)
    for r in rows:
        if r.get("status") == "planned":
            continue
        if r.get("module"):
            modules.setdefault(r["module"], {"tests": 0})["tests"] += 1
        for f in r.get("flows") or []:
            entry = flows.setdefault(f, {"tests": 0, "priority": None})
            entry["tests"] += 1
            if f not in registered and r.get("priority"):
                # Without the registry, the best priority among the flow's rows ("P0" < "P1" < "P2").
                entry["priority"] = min(filter(None, [entry["priority"], r["priority"]]))
    return dict(sorted(modules.items())), dict(sorted(flows.items()))


def compute(files: dict[str, str], rows: list[dict[str, Any]], today: dt.date, *, bugs: dict[str, Any],
            coverage: dict[str, float | None], cfg: Any = None, pr_walls: list[float] | None = None) -> dict[str, Any]:
    cutoff = dt.datetime.combine(today - dt.timedelta(days=WINDOW_DAYS), dt.time.min, tzinfo=dt.timezone.utc).timestamp()
    recent = [r for r in history.runs(files) if isinstance(r.get("ts"), (int, float)) and r["ts"] >= cutoff]

    trend: dict[tuple[str, str], dict[str, Any]] = {}
    walls: dict[str, list[float]] = defaultdict(list)
    for r in recent:
        day = shadow._day(r["ts"]).isoformat()
        trig = r.get("trigger") or "local"
        t = trend.setdefault((day, trig), {"date": day, "trigger": trig, "runs": 0, **{o: 0 for o in OUTCOMES}})
        t["runs"] += 1
        for o in OUTCOMES:
            t[o] += int((r.get("counts") or {}).get(o, 0))
        walls[trig] += [float(r["wall_seconds"])] if isinstance(r.get("wall_seconds"), (int, float)) else []

    suite_of = {r["id"]: r.get("suite") or "unknown" for r in rows}
    flakes: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    # Tests seen in the window only; each still counts its last 10 outcomes, which may be older.
    seen_since = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    for tid, w in history.tests_seen(files, seen_since).items():
        letters = w.get("o", "")
        s = flakes[suite_of.get(tid, "unknown")]
        s[0] += letters.count("k")
        s[1] += len(letters)
    k, n = sum(v[0] for v in flakes.values()), sum(v[1] for v in flakes.values())

    auto = history.quarantine_entries(files)
    quarantined = {e["id"] for e in auto if e.get("id") and not e.get("expired") and quarantine._expires(e) >= today}
    quarantined |= {r["id"] for r in rows if r.get("status") == "quarantined"}

    duration = {t: {"runs": sum(x["runs"] for x in trend.values() if x["trigger"] == t), "p50": percentile(v, 50), "p95": percentile(v, 95)}
                for t, v in sorted(walls.items())}
    if pr_walls:  # PR jobs never write history, so their durations come from the run list
        duration["pr"] = {"runs": len(pr_walls), "p50": percentile(pr_walls, 50), "p95": percentile(pr_walls, 95)}
    modules, flows = _coverage(rows, cfg)
    return {
        "schema": "qa-metrics/v1",
        "generated": today.isoformat(),
        "window_days": WINDOW_DAYS,
        "coverage": {"modules": modules, "flows": flows, "line": dict(sorted(coverage.items())),
                     "api_operations": {"hit_rate": None, "source": "not measured"}},
        "trend": [trend[key] for key in sorted(trend)],
        "flake": {"rate": round(k / n, 4) if n else 0.0,
                  "by_suite": {s: round(v[0] / v[1], 4) if v[1] else 0.0 for s, v in sorted(flakes.items())},
                  "quarantined": sorted(quarantined)},
        "duration": duration,
        "bugs": bugs,
        "enforcement": shadow.enforcement(history._jsonl(files.get("shadow.jsonl", "")), today),
    }


def _table(head: list[str], rows: list[list[Any]]) -> str:
    cells = [head, ["---"] * len(head), *[["" if c is None else str(c) for c in r] for r in rows]]
    return "\n".join("| " + " | ".join(r) + " |" for r in cells)


def summary(metrics: dict[str, Any]) -> str:
    e, f, b, cov = metrics["enforcement"], metrics["flake"], metrics["bugs"], metrics["coverage"]
    out = [f"## QA metrics, last {metrics['window_days']} days ({metrics['generated']})", "",
           f"Enforcement: **{e['status']}**. {e['reason']}. {e.get('population', '')}".rstrip(), ""]
    out += ["### Trend", "", _table(["Date", "Trigger", "Runs", *OUTCOMES],
                                    [[t["date"], t["trigger"], t["runs"], *(t[o] for o in OUTCOMES)] for t in metrics["trend"][-14:]]), ""]
    out += ["### Flaky tests", "", f"Flake rate {f['rate']}. Quarantined: {len(f['quarantined'])}.", "",
            _table(["Suite", "Flake rate"], [[s, r] for s, r in f["by_suite"].items()]), ""]
    out += ["### Duration (wall seconds)", "", _table(["Trigger", "Runs", "p50", "p95"],
                                                      [[t, d["runs"], d["p50"], d["p95"]] for t, d in metrics["duration"].items()]), ""]
    untested = [fl for fl, d in cov["flows"].items() if not d["tests"]]
    out += ["### Coverage", "", f"Flows without tests: {', '.join(untested) or 'none'}. API operations: not measured.", "",
            _table(["Suite", "Line coverage %"], [[s, "not measured" if p is None else p] for s, p in cov["line"].items()]), ""]
    if "error" in b:
        out += ["### Bugs", "", f"Caught and escaped bugs: not measured ({b['error']}).", ""]
    else:
        note = ["Caught is 0. It stays 0 while the PR gate only plans and runs no tests.", ""] if not b["caught"] else []
        out += ["### Bugs", "", *note, _table(["Caught by the PR gate", "Escaped", "Escaped, no flow"],
                                       [[b["caught"], b["escaped"], b["unlinked"]]]), ""]
    return "\n".join(out)

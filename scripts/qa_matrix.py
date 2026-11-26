#!/usr/bin/env python3
"""QA-FINAL: the final QA pass's matrix, its checks, and its summary after a run.

    python3 scripts/qa_matrix.py --check     the data is whole and true to the tree, QA-FINAL.md is up to date
    python3 scripts/qa_matrix.py --write     render docs/test-plan/QA-FINAL.md from the data
    python3 scripts/qa_matrix.py --check --status TRACKER.md
                                             also: every follow-up and fix row the data names is a row of that
                                             project tracker, and every fix row is done

The data is docs/test-plan/data/qa-final.json: the lanes (where each runs), one row per surface and dimension (what
proves it, which lanes run that proof, its gap) and the reported cases with the regression tests that hold them. The
checks keep it from rotting: every proof path exists, every regression test still holds the case it names, every lane
a row names is one `qa_full.py` runs or a named place it cannot (CI, the reference host, a live model), and every gap
names, by its id, the follow-up row that tracks it. `summarize` reads a `qa_full.py` report and marks each row: proven here, failed,
not run, or what it still needs.

Standard library only (the scripts job runs scripts/test_qa_matrix.py with no venv).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "test-plan" / "data" / "qa-final.json"
DOC = ROOT / "docs" / "test-plan" / "QA-FINAL.md"

SURFACES = [
    ("chat", "Default chat"),
    ("agents", "Saved agents"),
    ("engine-jobs", "Engine jobs"),
    ("approvals", "Approvals and asks"),
    ("mcp", "MCP and connectors"),
    ("bg-ui", "Background agents UI"),
    ("jobs-page", "Jobs page"),
    ("admin", "Admin and settings"),
    ("ops", "Installs and ops"),
    ("erasure", "Data protection and erasure"),
    ("keys", "Key rotation"),
]
DIMENSIONS = [
    ("function", "Function"),
    ("performance", "Performance"),
    ("ux", "UX and accessibility"),
    ("latency", "Latency"),
    ("accuracy", "Accuracy"),
    ("parity", "Recorded parity"),
]
GAPS = {
    "none": "none",
    "partial": "partial",
    "missing": "missing",
    "live-model": "needs live model",
    "reference-host": "needs reference host",
    "ci-only": "CI only",
    "n/a": "not applicable",
}
EDITIONS = ("both", "oss")
WHERE = ("local", "ci", "reference-host", "live")
# A gap the local pass cannot close: its row's verdict names what it waits on.
WAITS = {"live-model": "needs live model", "reference-host": "needs reference host", "ci-only": "needs CI"}


def load(path: Path = DATA) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def row_id(row: dict[str, Any]) -> str:
    return f"{row['surface']}/{row['dimension']}"


def status_rows(path: Path) -> dict[str, str]:
    """A project tracker's rows by their first cell, each with its status: the fourth cell of a tracked row (id, priority,
    what, status, ...), the second of a package's row (id, "done on ...")."""
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and not set(cells[0]) <= set("-: "):
            rows.setdefault(cells[0], cells[3] if len(cells) > 3 else cells[1])
    return rows


def validate(data: dict[str, Any], *, root: Path = ROOT, run_lanes: set[str] | None = None,
             status: dict[str, str] | None = None) -> list[str]:
    """Every problem with the data, as one line each; none when it is whole and true to the tree."""
    problems: list[str] = []
    lanes = {lane["id"]: lane for lane in data.get("lanes", [])}
    for lane in lanes.values():
        if lane.get("where") not in WHERE:
            problems.append(f"lane {lane['id']}: where {lane.get('where')!r} is not one of {WHERE}")
        if run_lanes is not None and lane.get("where") == "local" and lane["id"] not in run_lanes:
            problems.append(f"lane {lane['id']}: local, but qa_full.py has no such lane")
    if run_lanes is not None:
        for missing in sorted(run_lanes - set(lanes)):
            problems.append(f"qa_full.py lane {missing} is not in the matrix's lanes")

    seen: Counter[str] = Counter(row_id(r) for r in data.get("rows", []))
    for surface, _ in SURFACES:
        for dimension, _ in DIMENSIONS:
            count = seen.get(f"{surface}/{dimension}", 0)
            if count != 1:
                problems.append(f"row {surface}/{dimension}: {count} rows, want exactly 1")
    known = {f"{s}/{d}" for s, _ in SURFACES for d, _ in DIMENSIONS}
    for rid in sorted(set(seen) - known):
        problems.append(f"row {rid}: not a surface and dimension of the matrix")

    for row in data.get("rows", []):
        rid = row_id(row)
        if row.get("editions") not in EDITIONS:
            problems.append(f"row {rid}: editions {row.get('editions')!r} is not one of {EDITIONS}")
        gap = row.get("gap")
        if gap not in GAPS:
            problems.append(f"row {rid}: gap {gap!r} is not one of {sorted(GAPS)}")
        if gap != "none" and not row.get("gapNote"):
            problems.append(f"row {rid}: gap {gap} says nothing of what is not proven (gapNote)")
        if gap not in ("none", "n/a") and not row.get("fu"):
            problems.append(f"row {rid}: gap {gap} names no follow-up row that tracks it (fu)")
        if gap != "n/a" and not row.get("lanes"):
            problems.append(f"row {rid}: no lane runs its proof")
        for lane in row.get("lanes", []):
            if lane not in lanes:
                problems.append(f"row {rid}: lane {lane} is not in the matrix's lanes")
        if gap == "none":
            local = [x for x in row.get("lanes", []) if lanes.get(x, {}).get("where") == "local"]
            if not local:
                problems.append(f"row {rid}: gap none, but no local lane runs its proof")
            if not row.get("proof"):
                problems.append(f"row {rid}: gap none, but it names no proof")
        for proof in row.get("proof", []):
            if not (root / proof).exists():
                problems.append(f"row {rid}: proof {proof} does not exist")
        if status is not None:
            for fu in row.get("fu", []):
                if fu not in status:
                    problems.append(f"row {rid}: fu {fu} is not a tracker row")

    for report in data.get("reportedCases", []):
        rid = report["id"]
        if not report.get("tests"):
            problems.append(f"reported case {rid}: no regression test")
        levels = set()
        for test in report.get("tests", []):
            path = root / test["path"]
            if test.get("lane") not in lanes:
                problems.append(f"reported case {rid}: {test['path']}: lane {test.get('lane')!r} is not in the matrix's lanes")
            if not path.is_file():
                problems.append(f"reported case {rid}: {test['path']} does not exist")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in test.get("contains", []):
                if needle not in text:
                    problems.append(f"reported case {rid}: {test['path']} no longer holds {needle!r}")
            levels.add(test.get("lane"))
        if report.get("tests") and "regressions" not in levels:
            problems.append(f"reported case {rid}: no test in the regressions lane (one that needs no stack)")
        if status is not None:
            for name in report.get("statusRows", []):
                state = status.get(name)
                if state is None:
                    problems.append(f"reported case {rid}: tracker row {name!r} not found")
                elif not re.match(r"DONE\b|done\b", state):
                    problems.append(f"reported case {rid}: tracker row {name!r} is not DONE ({state[:40]!r})")
    return problems


def regression_tests(data: dict[str, Any], lane: str = "regressions") -> dict[str, list[str]]:
    """The reported cases' tests a lane runs, by runner: `python` (from backend/python), `node` (from
    backend/nodejs/apps), `vitest` (from frontend), `other` (repo-relative)."""
    out: dict[str, list[str]] = {"python": [], "node": [], "vitest": [], "other": []}
    prefixes = (("backend/python/", "python"), ("backend/nodejs/apps/", "node"), ("frontend/", "vitest"))
    for report in data.get("reportedCases", []):
        for test in report.get("tests", []):
            if test.get("lane") != lane:
                continue
            path = test["path"]
            for prefix, runner in prefixes:
                if path.startswith(prefix):
                    item = path[len(prefix):]
                    break
            else:
                runner, item = "other", path
            if item not in out[runner]:
                out[runner].append(item)
    return out


def counts(data: dict[str, Any]) -> dict[str, Any]:
    rows = data.get("rows", [])
    by_gap = Counter(r["gap"] for r in rows)
    by_dimension: dict[str, Counter[str]] = {d: Counter() for d, _ in DIMENSIONS}
    for r in rows:
        by_dimension.setdefault(r["dimension"], Counter())[r["gap"]] += 1
    return {"rows": len(rows), "byGap": {g: by_gap.get(g, 0) for g in GAPS},
            "byDimension": {d: {g: c.get(g, 0) for g in GAPS} for d, c in by_dimension.items()}}


# ---- rendering -------------------------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _code(path: str) -> str:
    return f"`{path}`"


def render(data: dict[str, Any]) -> str:
    lanes = {lane["id"]: lane for lane in data["lanes"]}
    rows = {row_id(r): r for r in data["rows"]}
    c = counts(data)
    out = [
        "# QA-FINAL: the final QA pass",
        "",
        "<!-- Generated by scripts/qa_matrix.py --write from docs/test-plan/data/qa-final.json; edit the data, not this file. -->",
        "",
        "Every surface the harness ships, in every dimension the final pass judges (function, performance, UX and "
        "accessibility, latency, accuracy, recorded parity), with what proves it today, the lanes that run that proof, "
        "and the gap that remains. The reported cases each name the regression tests that hold them, at the level the "
        "bug lived. `scripts/qa_matrix.py --check` keeps this true to the tree: every proof path exists, every "
        "regression test still holds the case it names, every lane is one the pass runs or a named place it cannot.",
        "",
        "## How to run the final pass",
        "",
        "```bash",
        "scripts/qa_final.sh --out <dir>                                      # every local lane, in order, then the summary",
        "scripts/qa_final.sh --out <dir> --sample                             # each lane on a small selection: proves the wiring",
        "scripts/qa_final.sh --list                                           # the lanes, in the order they run",
        "```",
        "",
        "Before the pass, on the tree under test: `scripts/qa_full.sh --out <dir> --lanes ux "
        "--update-ux-baselines`, so the captures compare with today's screens (baselines live outside git, and "
        "changes that alter a screen on purpose leave them behind). The human-stack lane reads the human-test stack "
        "(scripts/human-test-stack.sh); it is skipped where none is up.",
        "",
        "The pass writes `final.md` and `final.json` beside qa_full's `index.html` and `report.json`: each row below "
        "marked *proven here* (its gap is none and every local lane it names passed), *proven here, gap …* (its local "
        "lanes passed; the rest is the gap), *failed*, *not run*, or what it waits on (*needs live model*, *needs "
        "reference host*, *needs CI*). The lanes run fastest first, so a broken tree fails within minutes: the reported "
        "cases' regressions, then unit, contracts, replay, integration, frontend and stores, then the lanes that share one "
        "e2e stack, then the lanes that bring up their own (MCP hub, options matrix, engines, PostgreSQL and air gap, "
        "fault, perf), and the human-test stack's smoke last. No lane makes a paid or live model call.",
        "",
        "## Totals",
        "",
        f"{c['rows']} rows ({len(SURFACES)} surfaces × {len(DIMENSIONS)} dimensions).",
        "",
        "| Dimension | " + " | ".join(GAPS.values()) + " |",
        "|---|" + "---|" * len(GAPS),
    ]
    for d, title in DIMENSIONS:
        out.append(f"| {title} | " + " | ".join(str(c["byDimension"][d][g]) for g in GAPS) + " |")
    out.append("| **All** | " + " | ".join(f"**{c['byGap'][g]}**" for g in GAPS) + " |")
    out += ["", "## Matrix", ""]
    for s, stitle in SURFACES:
        out += [f"### {stitle}", "", "| Dimension | Editions | What proves it | Lanes | Gap | What is left | Tracked by |",
                "|---|---|---|---|---|---|---|"]
        for d, dtitle in DIMENSIONS:
            r = rows.get(f"{s}/{d}")
            if r is None:
                continue
            proof = "<br>".join(_code(p) for p in r.get("proof", []))
            if r.get("evidence"):
                proof = (proof + "<br>" if proof else "") + _cell(r["evidence"])
            out.append("| " + " | ".join([
                dtitle, r["editions"], proof or "—", ", ".join(r.get("lanes", [])), GAPS[r["gap"]],
                _cell(r.get("gapNote") or "—"), ", ".join(r.get("fu", [])) or "—"]) + " |")
        out.append("")
    out += ["## Reported cases and their regression tests", "",
            "| Report | Fix row | Where the bug lived | Regression tests (lane) |", "|---|---|---|---|"]
    for rep in data["reportedCases"]:
        tests = "<br>".join(f"{_code(t['path'])} ({t['lane']})" for t in rep["tests"])
        out.append(f"| {_cell(rep['report'])} | {_cell(', '.join(rep['statusRows']))} | {_cell(rep['level'])} | {tests} |")
    out += ["", "## Lanes", "", "| Lane | Where | What it runs | About (min) |", "|---|---|---|---|"]
    for lane in data["lanes"]:
        out.append(f"| `{lane['id']}` | {lane['where']} | {_cell(lane['title'])} | {lane.get('minutes', '—')} |")
    out.append("")
    return "\n".join(out)


# ---- the summary after a run -------------------------------------------------------------------------------


def lane_statuses(report: dict[str, Any]) -> dict[str, str]:
    return {lane["id"]: lane["status"] for lane in report.get("lanes", [])}


def verdict(row: dict[str, Any], lanes: dict[str, dict[str, Any]], ran: dict[str, str]) -> str:
    if row["gap"] == "n/a":
        return "not applicable"
    local = [x for x in row["lanes"] if lanes.get(x, {}).get("where") == "local"]
    states = [ran.get(x) for x in local]
    if any(s in ("failed", "error") for s in states):
        return "failed"
    if row["gap"] in WAITS and not local:
        return WAITS[row["gap"]]
    if not local or any(s is None or s == "skipped" for s in states):
        return "not run"
    if row["gap"] == "none":
        return "proven here"
    if row["gap"] in WAITS:
        return f"proven here, {WAITS[row['gap']]}"
    return f"proven here, gap {row['gap']}"


def summarize(data: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    lanes = {lane["id"]: lane for lane in data["lanes"]}
    ran = lane_statuses(report)
    rows = [{"id": row_id(r), "gap": r["gap"], "lanes": {x: ran.get(x, "not run") for x in r["lanes"]},
             "verdict": verdict(r, lanes, ran), "left": r.get("gapNote", ""), "fu": r.get("fu", [])}
            for r in data["rows"]]
    reports = []
    for rep in data["reportedCases"]:
        used = sorted({t["lane"] for t in rep["tests"]})
        states = {x: ran.get(x, "not run") for x in used}
        bad = any(s in ("failed", "error") for s in states.values())
        reports.append({"id": rep["id"], "lanes": states,
                        "verdict": "failed" if bad else ("held" if all(s in ("passed", "flaky") for s in states.values())
                                                          else "partly run")})
    return {"sample": bool(report.get("options", {}).get("sample")), "sha": report.get("sha", ""),
            "verdicts": dict(Counter(r["verdict"] for r in rows)), "rows": rows, "reportedCases": reports,
            "lanes": ran}


def render_summary(summary: dict[str, Any]) -> str:
    out = ["# QA-FINAL summary", "", f"Tree `{summary['sha'][:10]}`." + (
        " **Sample run**: each lane ran a small selection, which proves the wiring, not the rows." if summary["sample"] else ""),
        "", "| Verdict | Rows |", "|---|---|"]
    for name, n in sorted(summary["verdicts"].items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| {name} | {n} |")
    out += ["", "| Lane | Status |", "|---|---|"]
    out += [f"| `{k}` | {v} |" for k, v in summary["lanes"].items()]
    out += ["", "## Reported cases", "", "| Report | Lanes | Verdict |", "|---|---|---|"]
    for rep in summary["reportedCases"]:
        out.append(f"| {rep['id']} | {', '.join(f'{k} {v}' for k, v in rep['lanes'].items())} | {rep['verdict']} |")
    out += ["", "## Rows", "", "| Row | Gap | Lanes | Verdict | What is left | Tracked by |", "|---|---|---|---|---|---|"]
    for r in summary["rows"]:
        out.append(f"| {r['id']} | {GAPS[r['gap']]} | {', '.join(f'{k} {v}' for k, v in r['lanes'].items())} | "
                   f"{r['verdict']} | {_cell(r['left'] or '—')} | {', '.join(r['fu']) or '—'} |")
    out.append("")
    return "\n".join(out)


def write_summary(data: dict[str, Any], report: dict[str, Any], out: Path) -> dict[str, Any]:
    summary = summarize(data, report)
    (out / "final.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    (out / "final.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


def qa_full_lanes() -> set[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import qa_full  # noqa: PLC0415 - qa_full imports this module for its --final profile

    return {name for name, _, _ in qa_full.LANES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate the data and that QA-FINAL.md is up to date")
    group.add_argument("--write", action="store_true", help="render QA-FINAL.md")
    group.add_argument("--summary", type=Path, metavar="DIR", help="write final.md/json from DIR/report.json")
    parser.add_argument("--status", type=Path, metavar="TRACKER", help="with --check: a project tracker whose rows the "
                        "data's follow-up and fix rows must be")
    args = parser.parse_args(argv)
    data = load()
    if args.summary:
        report = json.loads((args.summary / "report.json").read_text(encoding="utf-8"))
        s = write_summary(data, report, args.summary)
        print(f"qa_matrix: {args.summary / 'final.md'}: {s['verdicts']}")
        return 0
    problems = validate(data, run_lanes=qa_full_lanes(),
                        status=None if args.status is None else status_rows(args.status))
    text = render(data)
    if args.write:
        DOC.write_text(text, encoding="utf-8")
        print(f"qa_matrix: wrote {DOC.relative_to(ROOT)}")
    elif not DOC.is_file() or DOC.read_text(encoding="utf-8") != text:
        problems.append(f"{DOC.relative_to(ROOT)} is out of date: run scripts/qa_matrix.py --write")
    for line in problems:
        print(f"qa_matrix: {line}", file=sys.stderr)
    c = counts(data)
    print(f"qa_matrix: {c['rows']} rows, " + ", ".join(f"{GAPS[g]} {n}" for g, n in c["byGap"].items())
          + f"; {len(data.get('reportedCases', []))} reported cases" + ("" if not problems else f"; {len(problems)} problems"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

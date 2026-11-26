#!/usr/bin/env python3
"""R31-JOURNEY (ADR-TEST-01): the feature registry's cells, what covers each, and the journey runner's plan.

    python3 scripts/journey_matrix.py --write    render docs/test-plan/generated/journeys.md and journey-plan.json
    python3 scripts/journey_matrix.py --check    non-zero when a cell has no cover, a cover names a test that does not
                                                 exist, a gap is stale, or a render is out of date
    python3 scripts/journey_matrix.py --check --status TRACKER.md
                                                 also: a gap or known red names no row of that project tracker
    python3 scripts/journey_matrix.py --stats    the cell counts, covered before (listed tests alone) and after

The registry is docs/test-plan/data/journeys.json: features, paths (each with the account shapes it admits), shapes,
rules that mark a cell impossible or out of scope (with the reason), the existing real-stack tests that cover cells,
gaps (each with its follow-up row), the must-run cells (every owner-reported break), and the tiers.

A cell is (feature, path, shape) for a shape its path admits. Each one is exactly one of:

- excluded: the first rule that matches it says why (impossible, out_of_scope);
- covered: the journey runner (deployment/e2e/journeys.py) runs it in its full tier, or a listed test runs it end to
  end on a real stack with the scripted model;
- a gap: a `gaps` entry matches it and names, by its id, the follow-up row that tracks it.

What the runner runs is read from its literals (``SUPPORT``, ``DRIVEN``, ``TURNS``) without importing it, so this check
needs no stack and no third-party package; the runner refuses to start when its literals and its probes disagree.

Standard library only (the scripts job runs scripts/test_journey_matrix.py with no venv).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs/test-plan/data/journeys.json"
RUNNER = ROOT / "deployment/e2e/journeys.py"
SMOKE = ROOT / "deployment/e2e/smoke_human.py"
DOC = ROOT / "docs/test-plan/generated/journeys.md"
PLAN = ROOT / "docs/test-plan/generated/journey-plan.json"
# Where a test that drives a real stack lives: a unit test does not cover a cell, whatever it asserts.
REAL_STACK_ROOTS = ("integration-tests/harness/", "deployment/e2e/", "frontend/tests/e2e/")
RULE_KINDS = ("impossible", "out_of_scope")
REF = re.compile(r"^(?P<kind>pytest|playwright|smoke|script):(?P<rest>.+)$")

Cell = tuple[str, str, str]
Json = dict[str, Any]


def load(path: Path = DATA) -> Json:
    return json.loads(path.read_text())


# SKIP (feature -> "path/shape" the runner leaves out where it otherwise would run it) is optional.
RUNNER_LITERALS = ("SUPPORT", "DRIVEN", "TURNS", "SKIP")


def runner_literals(path: Path = RUNNER) -> Json:
    """The runner's ``SUPPORT`` (feature -> paths its probe runs on), ``DRIVEN`` (path -> shapes its driver sets up) and
    ``TURNS`` (turn -> the features it proves), read from the module's own assignments."""
    found: Json = {}
    if not path.exists():
        return {k: {} for k in RUNNER_LITERALS}
    for node in ast.parse(path.read_text()).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id in RUNNER_LITERALS and node.value is not None:
            found[target.id] = {k: list(v) for k, v in ast.literal_eval(node.value).items()}
    return {k: found.get(k, {}) for k in RUNNER_LITERALS}


def _matches(entry: Json, cell: Cell) -> bool:
    f, p, s = cell
    return all(not entry.get(key) or value in entry[key] for key, value in
               (("features", f), ("paths", p), ("shapes", s)))


@dataclass(frozen=True)
class Matrix:
    data: Json
    runner: Json

    @property
    def features(self) -> list[str]:
        return [f["id"] for f in self.data["features"]]

    @property
    def paths(self) -> dict[str, Json]:
        return {p["id"]: p for p in self.data["paths"]}

    @property
    def shapes(self) -> list[str]:
        return [s["id"] for s in self.data["shapes"]]

    def cells(self) -> list[Cell]:
        return [(f, p, s) for p, path in self.paths.items() for f in self.features for s in path["shapes"]]

    def rule_of(self, cell: Cell) -> Json | None:
        return next((r for r in self.data["rules"] if _matches(r, cell)), None)

    def feasible(self) -> list[Cell]:
        return [c for c in self.cells() if self.rule_of(c) is None]

    def runner_runs(self, cell: Cell) -> bool:
        f, p, s = cell
        return (p in self.runner["SUPPORT"].get(f, []) and s in self.runner["DRIVEN"].get(p, [])
                and f"{p}/{s}" not in self.runner.get("SKIP", {}).get(f, [])
                and any(f in feats for feats in self.runner["TURNS"].values()))

    def tested(self) -> dict[Cell, list[str]]:
        out: dict[Cell, list[str]] = defaultdict(list)
        for test in self.data["tests"]:
            for cell in test["cells"]:
                out[tuple(cell)].append(test["ref"])  # type: ignore[index]
        return out

    def covers(self) -> dict[Cell, list[str]]:
        """Each feasible cell's covers: the runner's journeys and the listed tests."""
        tested = self.tested()
        out: dict[Cell, list[str]] = {}
        for cell in self.feasible():
            refs = list(tested.get(cell, []))
            if self.runner_runs(cell):
                refs.insert(0, f"journey:{journey_id(cell[1], cell[2], turn_of(self.runner, cell[0]))}")
            out[cell] = refs
        return out

    def gap_of(self, cell: Cell) -> Json | None:
        return next((g for g in self.data["gaps"] if _matches(g, cell)), None)


def full_only(data: Json, journey: Json) -> Json | None:
    """The registry entry that leaves a journey to the full tier (``tiers.gate.fullOnly``: slow or redundant with a
    gate journey, each with its reason); a must-run cell's journey stays in the gate whatever it says."""
    return next((e for e in data["tiers"]["gate"].get("fullOnly", [])
                 if all(not e.get(key) or journey[field] in e[key]
                        for key, field in (("turns", "turn"), ("paths", "path"), ("shapes", "shape")))), None)


def turn_of(runner: Json, feature: str) -> str:
    return next(turn for turn, feats in runner["TURNS"].items() if feature in feats)


def journey_id(path: str, shape: str, turn: str) -> str:
    return f"J-{path}-{shape}-{turn}"


def plan(m: Matrix) -> Json:
    """The runner's journeys: one per (path, shape, turn) with the feasible features that turn proves there.

    full: every one. gate: per path, the primary shape's journeys, then another shape's for each feature the primary
    cannot carry, so every feasible (feature, path) the runner implements runs once; and every must-run cell."""
    journeys: dict[str, Json] = {}
    for cell in m.feasible():
        if not m.runner_runs(cell):
            continue
        f, p, s = cell
        turn = turn_of(m.runner, f)
        jid = journey_id(p, s, turn)
        journeys.setdefault(jid, {"id": jid, "path": p, "shape": s, "turn": turn, "features": []})["features"].append(f)
    gate: set[str] = set()
    for p, path in m.paths.items():
        order = [path["primaryShape"], *[s for s in path["shapes"] if s != path["primaryShape"]]]
        gated = [j for j in journeys.values() if j["path"] == p and full_only(m.data, j) is None]
        needed = {f for j in gated for f in j["features"]}
        for s in order:
            for j in sorted((j for j in gated if j["shape"] == s), key=lambda j: j["id"]):
                if needed & set(j["features"]):
                    gate.add(j["id"])
                    needed -= set(j["features"])
    for must in m.data["mustRun"]:
        for f, p, s in must["cells"]:
            if m.runner_runs((f, p, s)):
                gate.add(journey_id(p, s, turn_of(m.runner, f)))
    ordered = sorted(journeys.values(), key=lambda j: (list(m.paths).index(j["path"]), j["shape"], j["turn"]))
    return {
        "description": "Generated by scripts/journey_matrix.py --write from docs/test-plan/data/journeys.json and the "
                       "runner's literals; deployment/e2e/journeys.py runs it. Do not edit.",
        "tiers": {"gate": [j["id"] for j in ordered if j["id"] in gate], "full": [j["id"] for j in ordered]},
        "journeys": ordered,
    }


# --- checks ----------------------------------------------------------------------------------------------------------


def ref_problem(ref: str) -> str | None:
    """Why a test reference names nothing that runs on a real stack; None when it does."""
    match = REF.match(ref)
    if match is None:
        return f"{ref}: not kind:target (pytest, playwright, smoke, script)"
    kind, rest = match["kind"], match["rest"]
    if kind == "smoke":
        return None if json.dumps(rest)[1:-1] in SMOKE.read_text() or rest in SMOKE.read_text() else (
            f"{ref}: no such check label in {SMOKE.relative_to(ROOT)}")
    if "::" not in rest:
        return f"{ref}: needs <path>::<name>"
    file, name = rest.split("::", 1)
    if not file.startswith(REAL_STACK_ROOTS):
        return f"{ref}: {file} is not a real-stack test (under {', '.join(REAL_STACK_ROOTS)})"
    target = ROOT / file
    if not target.is_file():
        return f"{ref}: {file} does not exist"
    text = target.read_text()
    if kind in ("pytest", "script"):
        function, _, param = name.partition("[")
        if not re.search(rf"^\s*(async\s+)?def {re.escape(function)}\(", text, re.MULTILINE):
            return f"{ref}: no def {function}"
        return row_problem(ref, file, param.rstrip("]")) if param else None
    return None if name in text.replace("\\'", "'") else f"{ref}: no test titled {name!r} in {file}"


# The options matrix's runners take their rows from the committed arrays: a row id is a test only while it is a row.
MATRIX_ARRAYS = {"integration-tests/harness/e2e/test_pairwise.py": "e2e-pairwise.json",
                 "integration-tests/harness/e2e/test_threeway_core.py": "e2e-threeway-core.json"}


def row_problem(ref: str, file: str, param: str) -> str | None:
    array = MATRIX_ARRAYS.get(file)
    if array is None:
        return None
    rows = {f"{r['stack']}-{r['id']}" for r in json.loads((ROOT / "docs/test-plan/data" / array).read_text())["rows"]}
    return None if param in rows else f"{ref}: {param} is not a row of {array}"


def status_rows(path: Path) -> set[str]:
    """The row ids of a project tracker's Markdown tables (the first cell of each row)."""
    return {m.group(1) for m in re.finditer(r"^\| (?:\*\*)?([A-Z0-9][A-Za-z0-9._-]*?)(?:\*\*)? \|", path.read_text(), re.MULTILINE)}


SCHEMA = ROOT / "docs/test-plan/data/journeys.schema.json"


def shape_problems(data: Json, schema_path: Path = SCHEMA) -> list[str]:
    """The registry's required keys, from its schema (the standard library has no JSON Schema validator)."""
    schema = json.loads(schema_path.read_text())
    out = [f"journeys.json: no {key}" for key in schema["required"] if key not in data]
    for key, spec in schema["properties"].items():
        required = (spec.get("items") or {}).get("required") if spec.get("type") == "array" else spec.get("required")
        if key not in data or not required:
            continue
        entries = data[key] if spec.get("type") == "array" else [data[key]]
        out += [f"journeys.json {key}[{i}]: no {r}" for i, e in enumerate(entries) for r in required if r not in e]
    return out


def problems(m: Matrix, *, rows: set[str] | None = None) -> list[str]:
    """Every problem with the registry; a gap's or known red's tracker row is checked only when ``rows`` is given."""
    out: list[str] = []
    d = m.data
    for kind in ("features", "paths", "shapes"):
        ids = [x["id"] for x in d[kind]]
        out += [f"{kind}: {i} listed twice" for i, n in Counter(ids).items() if n > 1]
    features, paths, shapes = set(m.features), m.paths, set(m.shapes)
    for p, path in paths.items():
        out += [f"path {p}: shape {s} is not a shape" for s in path["shapes"] if s not in shapes]
        if path.get("primaryShape") not in path["shapes"]:
            out.append(f"path {p}: its primary shape {path.get('primaryShape')} is not one it admits")
    for entry, where in [*((r, f"rule {r['id']}") for r in d["rules"]), *((g, f"gap {g['id']}") for g in d["gaps"])]:
        for key, known in (("features", features), ("paths", set(paths)), ("shapes", shapes)):
            out += [f"{where}: {key[:-1]} {v} is not one" for v in entry.get(key, []) if v not in known]
    for r in d["rules"]:
        if r["kind"] not in RULE_KINDS:
            out.append(f"rule {r['id']}: kind {r['kind']} is not {RULE_KINDS}")
        if not r.get("reason"):
            out.append(f"rule {r['id']}: no reason")
        if not any(m.rule_of(c) is r for c in m.cells()):
            out.append(f"rule {r['id']}: decides no cell (an earlier rule or no cell matches it): drop it")
    runner = m.runner
    out += [f"runner SUPPORT: {f} is not a feature" for f in runner["SUPPORT"] if f not in features]
    out += [f"runner SUPPORT {f}: {p} is not a path" for f, ps in runner["SUPPORT"].items() for p in ps if p not in paths]
    out += [f"runner DRIVEN: {p} {s} is not a shape the path admits" for p, ss in runner["DRIVEN"].items()
            for s in ss if p not in paths or s not in paths[p]["shapes"]]
    turned = [f for feats in runner["TURNS"].values() for f in feats]
    out += [f"runner TURNS: {f} in two turns" for f, n in Counter(turned).items() if n > 1]
    out += [f"runner SUPPORT {f}: in no turn of TURNS" for f in runner["SUPPORT"] if f not in turned]
    feasible = set(m.feasible())
    tests_seen: set[str] = set()
    for test in d["tests"]:
        if test["ref"] in tests_seen:
            out.append(f"test {test['ref']}: listed twice")
        tests_seen.add(test["ref"])
        problem = ref_problem(test["ref"])
        if problem:
            out.append(f"test {problem}")
        for cell in test["cells"]:
            c = tuple(cell)
            if c not in set(m.cells()):
                out.append(f"test {test['ref']}: {list(c)} is not a cell (the path admits no such shape)")
            elif c not in feasible:
                out.append(f"test {test['ref']}: {list(c)} is excluded by {m.rule_of(c)['id']}: the rule or the "  # type: ignore[index]
                           "test is wrong")
    for must in d["mustRun"]:
        for cell in must["cells"]:
            if tuple(cell) not in feasible:
                out.append(f"must-run {must['id']}: {cell} is not a feasible cell")
            elif not m.runner_runs(tuple(cell)):  # type: ignore[arg-type]
                out.append(f"must-run {must['id']}: {cell} is not a journey the runner runs (a break must become one)")
    covers = m.covers()
    used_gaps: set[str] = set()
    for cell, refs in covers.items():
        if refs:
            continue
        gap = m.gap_of(cell)
        if gap is None:
            out.append(f"cell {list(cell)}: no test runs it and no gap tracks it")
        else:
            used_gaps.add(gap["id"])
    planned = {j["id"]: set(j["features"]) for j in plan(m)["journeys"]}
    for k in d.get("knownRed", []):
        if rows is not None and k["id"] not in rows:
            out.append(f"known red {k['id']}: no tracker row {k['id']}")
        for j in k["journeys"]:
            if j not in planned:
                out.append(f"known red {k['id']}: {j} is not a journey of the plan")
            elif not set(k["features"]) <= planned[j]:
                out.append(f"known red {k['id']}: {j} proves none of {sorted(set(k['features']) - planned[j])}")
    journeys = plan(m)["journeys"]
    for e in d["tiers"]["gate"].get("fullOnly", []):
        if not e.get("why"):
            out.append(f"full-only {e.get('id')}: no why")
        if not any(full_only({"tiers": {"gate": {"fullOnly": [e]}}}, j) for j in journeys):
            out.append(f"full-only {e.get('id')}: leaves no journey to the full tier: drop it")
    for g in d["gaps"]:
        if g["id"] not in used_gaps:
            out.append(f"gap {g['id']}: every cell it matches is covered or excluded now: drop it")
        if rows is not None and g["id"] not in rows:
            out.append(f"gap {g['id']}: no tracker row {g['id']}")
        if "**Waits on" not in g.get("why", "") and not g.get("waitsOn"):
            out.append(f"gap {g['id']}: says not what it waits on (waitsOn)")
    return out


# --- render ------------------------------------------------------------------------------------------------------------


def stats(m: Matrix) -> Json:
    cells, feasible, covers = m.cells(), m.feasible(), m.covers()
    tested = m.tested()
    excluded = Counter(m.rule_of(c)["kind"] for c in cells if m.rule_of(c) is not None)  # type: ignore[index]
    gate = set(plan(m)["tiers"]["gate"])
    gate_cells = {c for c in feasible if m.runner_runs(c)
                  and journey_id(c[1], c[2], turn_of(m.runner, c[0])) in gate}
    return {
        "features": len(m.features), "paths": len(m.paths), "shapes": len(m.shapes),
        "pathShapes": sum(len(p["shapes"]) for p in m.paths.values()),
        "cells": len(cells), "impossible": excluded["impossible"], "outOfScope": excluded["out_of_scope"],
        "feasible": len(feasible),
        "coveredBefore": sum(1 for c in feasible if tested.get(c)),
        "coveredByRunner": sum(1 for c in feasible if m.runner_runs(c)),
        "coveredAfter": sum(1 for c in feasible if covers[c]),
        "gaps": sum(1 for c in feasible if not covers[c]),
        "gateCells": len(gate_cells),
        "gateFeaturePaths": len({(c[0], c[1]) for c in gate_cells}),
        "featurePaths": len({(c[0], c[1]) for c in feasible}),
    }


def render(m: Matrix) -> str:
    s = stats(m)
    covers = m.covers()
    lines = [
        "# Journeys: feature x path x shape coverage",
        "",
        ("Generated by `scripts/journey_matrix.py --write` from `docs/test-plan/data/journeys.json` (ADR-TEST-01); do not "
         "edit. `--check` fails on a feasible cell nothing covers and no gap tracks."),
        "",
        (f"{s['features']} features x {s['paths']} paths, {s['pathShapes']} path-shapes: {s['cells']} cells, "
         f"{s['impossible']} impossible, {s['outOfScope']} out of scope, **{s['feasible']} feasible**. Covered: "
         f"{s['coveredBefore']} by the listed tests alone, {s['coveredByRunner']} by the journey runner, "
         f"**{s['coveredAfter']} in all**; {s['gaps']} tracked gaps. The gate tier runs {s['gateCells']} cells "
         f"({s['gateFeaturePaths']} of {s['featurePaths']} feasible feature x path pairs)."),
        "",
        ("Legend: `J` the journey runner, `T` a listed test, `JT` both, `-` a tracked gap (its id below), `x` "
         "impossible, `o` out of scope. A column is a path; each shape it admits is a sub-column."),
        "",
    ]
    cols = [(p, sh) for p, path in m.paths.items() for sh in path["shapes"]]
    lines.append("| feature | " + " | ".join(f"{p}<br>{sh}" for p, sh in cols) + " |")
    lines.append("|---|" + "---|" * len(cols))
    tested = m.tested()
    for f in m.features:
        row = []
        for p, sh in cols:
            cell = (f, p, sh)
            rule = m.rule_of(cell)
            if rule is not None:
                row.append("x" if rule["kind"] == "impossible" else "o")
            elif covers[cell]:
                row.append(("J" if m.runner_runs(cell) else "") + ("T" if tested.get(cell) else ""))
            else:
                row.append("-")
        lines.append(f"| {f} | " + " | ".join(row) + " |")
    lines += ["", "## Rules", ""]
    lines += [f"- **{r['id']}** ({r['kind']}): {r['reason']}" for r in m.data["rules"]]
    lines += ["", "## Gaps", ""]
    for g in m.data["gaps"]:
        n = sum(1 for c, refs in covers.items() if not refs and m.gap_of(c) is g)
        lines.append(f"- **{g['id']}** ({n} cells): {g['why']} **Waits on: {g.get('waitsOn', '')}.**")
    lines += ["", "## Must-run cells (owner-reported breaks)", ""]
    for must in m.data["mustRun"]:
        lines.append(f"- **{must['id']}** {must['title']}: " + ", ".join(
            "`" + " x ".join(c) + "`" for c in must["cells"]))
    lines += ["", "## Tiers", ""]
    p = plan(m)
    for tier, ids in p["tiers"].items():
        lines.append(f"- **{tier}**: {len(ids)} journeys ({m.data['tiers'][tier]['rule']})")
    for e in m.data["tiers"]["gate"].get("fullOnly", []):
        n = sum(1 for j in p["journeys"] if full_only({"tiers": {"gate": {"fullOnly": [e]}}}, j))
        lines.append(f"  - full tier only, **{e['id']}** ({n} journeys): {e['why']}")
    return "\n".join(lines) + "\n"


def write(m: Matrix) -> None:
    DOC.write_text(render(m))
    PLAN.write_text(json.dumps(plan(m), indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    group.add_argument("--stats", action="store_true")
    parser.add_argument("--status", type=Path, metavar="TRACKER", help="with --check: a project tracker whose rows "
                        "every gap and known red must name")
    args = parser.parse_args(argv)
    m = Matrix(load(), runner_literals())
    if args.stats:
        print(json.dumps(stats(m), indent=1))
        return 0
    if args.write:
        write(m)
        print(f"wrote {DOC.relative_to(ROOT)} and {PLAN.relative_to(ROOT)}")
        return 0
    found = shape_problems(m.data) + problems(m, rows=None if args.status is None else status_rows(args.status))
    if DOC.exists() is False or DOC.read_text() != render(m):
        found.append(f"{DOC.relative_to(ROOT)} is out of date: run scripts/journey_matrix.py --write")
    if not PLAN.exists() or json.loads(PLAN.read_text()) != plan(m):
        found.append(f"{PLAN.relative_to(ROOT)} is out of date: run scripts/journey_matrix.py --write")
    for problem in found:
        print(f"journey_matrix: {problem}", file=sys.stderr)
    s = stats(m)
    print(f"journey_matrix: {s['feasible']} feasible cells, {s['coveredAfter']} covered, {s['gaps']} tracked gaps"
          f"{'' if not found else f'; {len(found)} problems'}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

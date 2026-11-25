#!/usr/bin/env python3
"""List the agent harness's existing tests per component, so the test plan's trace can be regenerated.

    scripts/harness_test_inventory.py                 # write docs/test-plan/generated/inventory.{json,md}
    scripts/harness_test_inventory.py --check         # non-zero if the committed output or the gap totals
                                                      # (gap_totals.py) are stale, or a harness file is unclaimed
                                                      # (affected_tests.py --unclaimed)
    scripts/harness_test_inventory.py --component ADM # print one component's tests

The component map is data: docs/test-plan/data/components.json gives each component's source and test
globs. This script walks the test trees, assigns every test file to the components whose globs match it,
classifies it into a test level from its path and pytest markers, and counts test functions (Python: AST;
TypeScript: it()/test() calls). A harness test file that no component claims is listed as unmapped, so a
new suite cannot drop out of the trace unnoticed.

Standard library only; it reads files and never imports or runs them.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import warnings
from functools import lru_cache
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gap_totals  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ROOT / "docs/test-plan/data/components.json"
OUT_DIR = ROOT / "docs/test-plan/generated"

TEST_ROOTS = (
    "backend/python/tests",
    "backend/nodejs/apps/tests",
    "frontend/app",
    "frontend/lib",
    "frontend/tests/e2e",
    "integration-tests",
    "deployment",
    "scripts",
)
SKIP_DIRS = {"node_modules", ".venv", "__pycache__", ".git", "dist", "build", ".next", "fixtures", "golden", "goldens"}

# Test files under these prefixes belong to the harness or its touch points; an unmapped one is reported.
HARNESS_AREAS = (
    "backend/python/tests/unit/harness/",
    "backend/python/tests/unit/harness_core/",
    "backend/python/tests/unit/harness_engines/",
    "backend/python/tests/cohesion/",
    "backend/python/tests/contracts/",
    "backend/python/tests/harness_replay/",
    "backend/nodejs/apps/tests/modules/agent-harness/",
    "backend/nodejs/apps/tests/contracts/",
    "backend/nodejs/apps/tests/cohesion/",
    "integration-tests/harness/",
)

LEVELS = (
    "unit", "contract", "component", "cohesion", "replay", "integration", "integration-docker",
    "fault", "e2e-browser", "e2e-client", "perf", "security", "resilience", "deployment",
    "answer-quality", "answer-quality-live", "gate-slow", "live",
)


@dataclass
class TestFile:
    path: str
    language: str
    level: str
    markers: list[str] = field(default_factory=list)
    tests: int | None = None
    names: list[str] = field(default_factory=list)


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(".py"):
        return name.startswith("test_") or name.startswith("integration_test_")
    if name.endswith((".test.ts", ".test.tsx", ".itest.ts", ".spec.ts", ".bench.ts")):
        return True
    if rel.startswith("backend/nodejs/apps/tests/") and "/support/" in rel and name.endswith("-contract.ts"):
        return True
    if name.endswith(".sh") and rel.startswith("deployment/") and ("/tests/" in rel or rel.startswith("deployment/e2e/")):
        return True
    return False


def walk() -> list[str]:
    found = []
    for root in TEST_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for f in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, f), ROOT).replace(os.sep, "/")
                if is_test_file(rel):
                    found.append(rel)
    return sorted(set(found))


def _mark_names(node: ast.AST) -> list[str]:
    names = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Attribute):
            if isinstance(sub.value.value, ast.Name) and sub.value.value.id == "pytest" and sub.value.attr == "mark":
                names.append(sub.attr)
    return names


@lru_cache(maxsize=None)
def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _base_names(cls: ast.ClassDef) -> list[str]:
    out = []
    for b in cls.bases:
        if isinstance(b, ast.Name):
            out.append(b.id)
        elif isinstance(b, ast.Attribute):
            out.append(b.attr)
    return out


def _parse(text: str) -> ast.Module | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(text)
    except SyntaxError:
        return None


def class_index(paths: list[Path]) -> dict[str, tuple[list[str], list[str]]]:
    """Class name -> (base names, own test method names), over test files and contract-suite modules.

    Contract suites are written once and subclassed per adapter; a subclass usually has no test method of its own,
    so its tests are counted through its bases.
    """
    index: dict[str, tuple[list[str], list[str]]] = {}
    for path in paths:
        tree = _parse(read_text(path))
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                own = [i.name for i in node.body if isinstance(i, (ast.FunctionDef, ast.AsyncFunctionDef)) and i.name.startswith("test")]
                bases = _base_names(node)
                prev = index.get(node.name)
                if prev is None or len(own) > len(prev[1]):
                    index[node.name] = (bases, own)
    return index


def inherited_tests(name: str, index: dict, seen: set[str] | None = None) -> list[str]:
    seen = seen or set()
    if name in seen or name not in index:
        return []
    seen.add(name)
    bases, own = index[name]
    out = list(own)
    for b in bases:
        out += [t for t in inherited_tests(b, index, seen) if t not in out]
    return out


CLASS_INDEX: dict[str, tuple[list[str], list[str]]] = {}


def python_facts(text: str) -> tuple[list[str], list[str]]:
    tree = _parse(text)
    if tree is None:
        return [], []
    markers: list[str] = []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            markers += _mark_names(node.value)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            names.append(node.name)
            for d in node.decorator_list:
                markers += _mark_names(d)
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for d in node.decorator_list:
                markers += _mark_names(d)
            own = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    own.append(item.name)
                    for d in item.decorator_list:
                        markers += _mark_names(d)
            if not own:
                for b in _base_names(node):
                    own += [t for t in inherited_tests(b, CLASS_INDEX) if t not in own]
            names += [f"{node.name}.{t}" for t in own]
    ignore = {"parametrize", "asyncio", "usefixtures", "filterwarnings", "timeout", "skipif", "skip", "xfail"}
    return sorted(set(m for m in markers if m not in ignore)), names


TS_TEST = re.compile(r"""(?m)^\s*(?:it|test)(?:\.only|\.skip|\.each\([^)]*\))?\(\s*(['"`])(.+?)\1""")


def ts_facts(text: str) -> list[str]:
    return [m.group(2) for m in TS_TEST.finditer(text)]


def classify(rel: str, markers: list[str]) -> str:
    name = rel.rsplit("/", 1)[-1]
    if rel.startswith("frontend/tests/e2e/"):
        return "e2e-browser"
    if rel.startswith("frontend/"):
        return "unit"
    if rel.startswith("deployment/"):
        return "e2e-browser" if rel.startswith("deployment/e2e/") else "deployment"
    if rel.startswith("integration-tests/"):
        part = rel.split("/")[1]
        return {"harness": "fault", "perf": "perf", "security": "security", "resilience": "resilience",
                "ai_agents": "live", "mcp_package": "e2e-client"}.get(part, "integration")
    if rel.startswith("scripts/"):
        return "unit"
    if rel.startswith("backend/nodejs/"):
        if name.endswith(".bench.ts"):
            return "perf"
        if name.endswith(".itest.ts") or "/tests/integration/" in rel:
            return "integration"
        if "/tests/cohesion/" in rel:
            return "cohesion"
        if "/tests/contracts/" in rel or name.endswith("-contract.ts") or ".contract." in name:
            return "contract"
        if name.endswith(".http.test.ts"):
            return "component"
        return "unit"
    # backend/python
    sub = rel[len("backend/python/tests/"):].split("/", 1)[0]
    if "slow" in markers:
        return "answer-quality-live" if sub == "evals" else "gate-slow"
    if sub == "cohesion":
        return "cohesion"
    if sub == "contracts":
        return "contract"
    if sub == "harness_replay":
        return "replay"
    if sub == "evals":
        return "answer-quality"
    if sub == "integration":
        return "integration"
    if "docker" in markers:
        return "integration-docker"
    if "integration" in markers:
        return "integration"
    if "contract" in name:
        return "contract"
    return "unit"


def inspect(rel: str) -> TestFile:
    text = read_text(ROOT / rel)
    if rel.endswith(".py"):
        markers, names = python_facts(text)
        return TestFile(rel, "python", classify(rel, markers), markers, len(names), names)
    if rel.endswith((".ts", ".tsx")):
        names = ts_facts(text)
        return TestFile(rel, "typescript", classify(rel, []), [], len(names), names)
    return TestFile(rel, "shell", classify(rel, []), [], None, [])


def build() -> dict:
    data = json.loads(COMPONENTS.read_text())
    rels = walk()
    suite_modules = [p for p in (ROOT / "backend/python/harness_core/testing").rglob("*.py")]
    suite_modules += [ROOT / r for r in rels if r.endswith(".py")]
    suite_modules += list((ROOT / "backend/python/tests").rglob("*contract*.py"))
    CLASS_INDEX.clear()
    CLASS_INDEX.update(class_index(sorted(set(suite_modules))))
    files = {rel: inspect(rel) for rel in rels}
    all_paths = None
    claimed: set[str] = set()
    components = []
    for comp in data["components"]:
        test_res = [(g, glob_to_regex(g)) for g in comp["tests"]]
        mine = sorted(rel for rel in files if any(rx.match(rel) for _, rx in test_res))
        claimed.update(mine)
        dead_test_globs = [g for g, rx in test_res if not any(rx.match(rel) for rel in files)]
        if all_paths is None:
            all_paths = source_paths()
        dead_source = [g for g in comp["source"] if not _matches_any(glob_to_regex(g), all_paths)]
        levels = Counter()
        tests_by_level = Counter()
        markers = Counter()
        for rel in mine:
            tf = files[rel]
            levels[tf.level] += 1
            tests_by_level[tf.level] += tf.tests or 0
            markers.update(tf.markers)
        components.append({
            "id": comp["id"], "name": comp["name"], "status": comp["status"],
            "files": len(mine), "tests": sum(files[r].tests or 0 for r in mine),
            "filesByLevel": dict(sorted(levels.items())), "testsByLevel": dict(sorted(tests_by_level.items())),
            "markers": dict(sorted(markers.items())),
            "unmatchedTestGlobs": dead_test_globs, "unmatchedSourceGlobs": dead_source,
            "testFiles": [{"path": r, "level": files[r].level, "markers": files[r].markers, "tests": files[r].tests} for r in mine],
        })
    unmapped = sorted(rel for rel in files if rel.startswith(HARNESS_AREAS) and rel not in claimed)
    totals = Counter(tf.level for tf in files.values() if tf.path in claimed)
    return {
        "generatedBy": "scripts/harness_test_inventory.py",
        "componentMap": str(COMPONENTS.relative_to(ROOT)),
        "harnessTestFiles": len([r for r in files if r.startswith(HARNESS_AREAS)]),
        "claimedTestFiles": len(claimed),
        "claimedTests": sum(files[r].tests or 0 for r in claimed),
        "claimedFilesByLevel": dict(sorted(totals.items())),
        "components": components,
        "unmappedHarnessTestFiles": unmapped,
    }


def _matches_any(rx: re.Pattern[str], paths: list[str]) -> bool:
    return any(rx.match(p) for p in paths)


def source_paths() -> list[str]:
    paths = []
    for top in ("backend/python/app", "backend/python/harness_core", "backend/python/harness_engines",
                "backend/nodejs/apps/src", "frontend/app", "frontend/lib", "contracts", "deployment", "scripts",
                "backend/python/tests/support", "backend/nodejs/apps/tests/helpers",
                "backend/nodejs/apps/tests/modules/agent-harness", "backend/nodejs/apps/tests/modules/control-plane",
                "backend/python/tests/unit/harness", "backend/python/tests/unit/harness_core",
                "backend/python/tests/unit/harness_engines"):
        base = ROOT / top
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in {"node_modules", "__pycache__", ".git", ".next"}]
            for f in filenames:
                paths.append(os.path.relpath(os.path.join(dirpath, f), ROOT).replace(os.sep, "/"))
    return paths


def render_md(inv: dict) -> str:
    cols = [lv for lv in LEVELS if any(lv in c["filesByLevel"] for c in inv["components"])]
    lines = [
        "# Test inventory per component (generated)",
        "",
        f"Generated by `{inv['generatedBy']}` from `{inv['componentMap']}`. Do not edit by hand: change the map, then run",
        "`python3 scripts/harness_test_inventory.py` (and `--check` in review).",
        "",
        f"- Test files in the harness areas: **{inv['harnessTestFiles']}**; files claimed by a component (any area): **{inv['claimedTestFiles']}**; "
        f"test functions in them: **{inv['claimedTests']}**.",
        f"- Unmapped harness test files: **{len(inv['unmappedHarnessTestFiles'])}** (listed at the end).",
        "- Cells are `files/tests`. A file may count for more than one component (cohesion and contract suites often do).",
        "- Levels come from the path and pytest markers: `integration-docker` = `-m docker`; `gate-slow` = `-m slow` outside evals;",
        "  `fault` = `integration-tests/harness/fault`; shell scripts count as files with no test count.",
        "",
        "| Component | Status | Files | Tests | " + " | ".join(cols) + " |",
        "|---|---|---|---|" + "|".join("---" for _ in cols) + "|",
    ]
    for c in inv["components"]:
        cells = []
        for lv in cols:
            f = c["filesByLevel"].get(lv)
            cells.append(f"{f}/{c['testsByLevel'].get(lv, 0)}" if f else "–")
        lines.append(f"| {c['id']} {c['name']} | {c['status']} | {c['files']} | {c['tests']} | " + " | ".join(cells) + " |")
    lines += ["", "## Stale globs", "",
              "Globs in the component map that match nothing. A test glob here names a planned suite (control-plane WPs) or a path that moved.", ""]
    for c in inv["components"]:
        if c["unmatchedTestGlobs"] or c["unmatchedSourceGlobs"]:
            lines.append(f"- **{c['id']}**: tests {c['unmatchedTestGlobs'] or '[]'}; source {c['unmatchedSourceGlobs'] or '[]'}")
    lines += ["", "## Files per component", ""]
    for c in inv["components"]:
        lines.append(f"### {c['id']} {c['name']}")
        lines.append("")
        if not c["testFiles"]:
            lines.append("No test file yet.")
        for tf in c["testFiles"]:
            mk = f" `{','.join(tf['markers'])}`" if tf["markers"] else ""
            n = "" if tf["tests"] is None else f" ({tf['tests']})"
            lines.append(f"- `{tf['path']}` {tf['level']}{mk}{n}")
        lines.append("")
    lines += ["## Unmapped harness test files", ""]
    lines += [f"- `{p}`" for p in inv["unmappedHarnessTestFiles"]] or ["None."]
    return "\n".join(lines) + "\n"


def generated_docs_fresh() -> bool:
    """Gap totals (05) and every harness file claimed."""
    import affected_tests  # imports this module, so not at the top

    unclaimed = affected_tests.unclaimed_harness_files(ROOT)
    for path in unclaimed:
        print(f"unclaimed harness file: {path} (claim it in {COMPONENTS.relative_to(ROOT)})", file=sys.stderr)
    results = [gap_totals.main(["--check"]), 1 if unclaimed else 0]
    return not any(results)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the committed inventory differs from a fresh run")
    ap.add_argument("--component", help="print the test files of one component id")
    args = ap.parse_args(argv)
    inv = build()
    if args.component:
        for c in inv["components"]:
            if c["id"] == args.component:
                for tf in c["testFiles"]:
                    print(f"{tf['level']:<18} {tf['tests'] if tf['tests'] is not None else '-':>4}  {tf['path']}")
                return 0
        print(f"unknown component {args.component}", file=sys.stderr)
        return 2
    js = json.dumps(inv, indent=1, sort_keys=False) + "\n"
    md = render_md(inv)
    jpath, mpath = OUT_DIR / "inventory.json", OUT_DIR / "inventory.md"
    if args.check:
        stale = [p for p, body in ((jpath, js), (mpath, md)) if not p.exists() or p.read_text() != body]
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)} (run scripts/harness_test_inventory.py)", file=sys.stderr)
        return 1 if stale or not generated_docs_fresh() else 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jpath.write_text(js)
    mpath.write_text(md)
    print(f"{inv['claimedTestFiles']} files, {inv['claimedTests']} tests over {len(inv['components'])} components; "
          f"{len(inv['unmappedHarnessTestFiles'])} unmapped harness files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The floor: tests a change needs, decided by rules alone. A model can add to
it later; nothing removes from it."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from qa import globs, graphs
from qa.config import Config, list_files

PROFILE_ORDER = ["fast", "standard", "conservative", "full"]
# Text files here are read at runtime (skill packs, prompts), so they are not docs.
PRODUCT_DIRS = ("backend/", "frontend/", "integration-tests/", "deployment/")
UNIT_FAMILIES = {"pytest", "mocha", "vitest"}
MAX_REASONS = 3
MAX_CANDIDATES = 300
CODE = re.compile(r"\.(py|ts|tsx|js|jsx|mjs|cjs)$")
# Declarations with no behaviour of their own; a missing mirror test is not a gap.
NO_UNIT_TEST = re.compile(
    r"(^|/)(__init__\.py|constants?\.(py|ts)|enums?\.(py|ts)|index\.tsx?|[^/]*\.d\.ts|"
    r"[^/]*\.?types?\.tsx?|[^/]*\.(interface|schema|dto)\.ts)$|/(types|constants|interfaces|schemas?)/"
)


class Floor:
    def __init__(self, rows: dict[str, dict[str, Any]], in_scope) -> None:
        self.rows = rows
        self.in_scope = in_scope
        self.items: dict[str, dict[str, Any]] = {}
        self.by_file: dict[str, int] = defaultdict(int)

    def add(self, row: dict[str, Any], reason: str, source: str | None = None, grep: str | None = None) -> None:
        if not self.in_scope(row) or row.get("status") == "planned":
            return
        item = self.items.get(row["id"])
        if item is None:
            item = self.items[row["id"]] = {"why": [], "grep": grep}
        elif item["grep"] and not grep:
            item["grep"] = None
        if reason not in item["why"] and len(item["why"]) < MAX_REASONS:
            item["why"].append(reason)
        if source:
            self.by_file[source] += 1


def _upgrade(current: str, wanted: str) -> str:
    return wanted if PROFILE_ORDER.index(wanted) > PROFILE_ORDER.index(current) else current


def effective_profile(cfg: Config, requested: str, branch: str) -> tuple[str, str | None]:
    for pattern, prof in cfg.always.get("branch_overrides", {}).items():
        if globs.match(branch, pattern):
            up = _upgrade(requested, prof)
            if up != requested:
                return up, f"branch {branch} matches {pattern}"
    return requested, None


def mirror_test(path: str, mirror: dict[str, str]) -> str | None:
    src, test = mirror["src"], mirror["test"]
    if not path.startswith(src):
        return None
    rel = path[len(src):]
    d, name = posixpath.split(rel)
    stem, ext = posixpath.splitext(name)
    if mirror.get("style") == "py":
        if ext != ".py" or stem == "__init__":
            return None
        return posixpath.join(test, d, f"test_{stem}.py")
    if ext not in (".ts", ".tsx"):
        return None
    return posixpath.join(test, d, f"{stem}.test{ext}")


def _name_root(path: str) -> str:
    """toolset_audit.py -> "toolset"; a short first word keeps the whole stem (cm_routes)."""
    stem = posixpath.basename(path).split(".")[0]
    first = re.split(r"[_\-]|(?<=[a-z])(?=[A-Z])", stem)[0]
    return first.lower() if len(first) >= 4 else stem


def _mirror_hit(f: str, edition: str, suite: dict[str, Any], mirror: dict[str, str], by_path, by_suite, floor: Floor) -> bool:
    test = mirror_test(f, mirror)
    if by_path.get(test):
        for r in by_path[test]:
            floor.add(r, f"mirror of {f}", f)
        return True
    # Many suites mirror by file name but not by directory; a test with
    # the mirror's name anywhere in the suite counts as the mirror. A base file and
    # its overlay twin share a name, so the test's edition must match too.
    named = [r for r in by_suite.get(suite["id"], [])
             if posixpath.basename(r["path"]) == posixpath.basename(test) and edition in (r["edition"], "switch")]
    for r in named:
        floor.add(r, f"same name as {f}", f)
    return bool(named)


def compute(
    cfg: Config,
    changeset: dict[str, Any],
    rows: list[dict[str, Any]],
    profile: str = "standard",
    scope: str = "all",
    history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    history = history or {}
    by_id = {r["id"]: r for r in rows}
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_suite: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_path[r["path"]].append(r)
        by_suite[r["suite"]].append(r)

    def in_scope(row: dict[str, Any]) -> bool:
        return scope == "all" or row["edition"] == scope

    floor = Floor(by_id, in_scope)
    files = [f["path"] for f in changeset["files"]]
    src_files = [f["path"] for f in changeset["files"] if f["kind"] in ("src", "config", "infra")]

    profile, override = effective_profile(cfg, profile, changeset.get("branch", ""))
    prof = cfg.profile(profile)
    notes: list[str] = [override] if override else []

    full_suites: dict[str, str] = {}
    all_suites = [s["id"] for s in cfg.suites]
    if prof.get("full"):
        full_suites.update({s: "full profile" for s in all_suites})
    if len(files) > cfg.always.get("max_files", 300):
        full_suites.update({s: f"{len(files)} files changed" for s in all_suites})
    for fr in cfg.always.get("full_run", []):
        hit = next((f for f in files if globs.match_any(f, fr["paths"])), None)
        if hit:
            targets = all_suites if fr["suites"] == "*" else fr["suites"]
            for s in targets:
                full_suites.setdefault(s, f"tracked file {hit}")
    # An edition switch file re-runs the unit suites of both editions that live
    # in the same part of the tree (a Python switch file does not touch vitest).
    for hit in (f for f in files if cfg.edition(f) == "switch"):
        for s in cfg.suites:
            if s["family"] in UNIT_FAMILIES and not s.get("stack") and hit.startswith(s.get("cwd", ".") + "/"):
                full_suites.setdefault(s["id"], f"edition switch file {hit}")
    for suite_id, why in full_suites.items():
        for r in by_suite.get(suite_id, []):
            floor.add(r, f"full run: {why}")

    for f in files:
        for r in by_path.get(f, []):
            floor.add(r, "changed test", f)

    stack = {"graph_db": prof.get("graph_db", "neo4j"), "broker": None}
    matrix: set[str] = set()
    matched_rules: list[str] = []
    for rule in cfg.rules:
        hits = [f for f in files if globs.match_any(f, rule.get("paths", []))]
        if not hits:
            continue
        matched_rules.append(rule["id"])
        why = f"rule {rule['id']}"
        src = hits[0]
        for f in hits:
            floor.by_file[f] += 1
        for pattern in rule.get("tests", []):
            for r in rows:
                if globs.match(r["path"], pattern):
                    floor.add(r, why, src)
        markers = set(rule.get("markers", []))
        if markers:
            for s in (s for s in cfg.suites if s.get("marker_rules")):
                for r in by_suite.get(s["id"], []):
                    if markers & set(r["markers"]):
                        floor.add(r, why, src)
        if rule.get("it_all"):
            for r in by_suite.get("it-stack", []):
                floor.add(r, why, src)
        for suite_id in rule.get("suites", []):
            for r in by_suite.get(suite_id, []):
                floor.add(r, why, src)
        matrix.update(rule.get("matrix", []))
        if rule.get("graph_db") == "both" or (rule.get("graph_db") and rule["graph_db"] != stack["graph_db"]):
            stack["graph_db"] = "both"
        if rule.get("broker"):
            stack["broker"] = rule["broker"]

    # Module paths never trigger a flow; only its own triggers do. Critical flows run on every change.
    flows: dict[str, str] = {}
    for fl in cfg.flows:
        hit = next((f for f in files if globs.match_any(f, fl.get("triggers", []))), None)
        if hit:
            flows[fl["id"]] = f"trigger {hit}"
        elif fl.get("critical"):
            flows[fl["id"]] = "critical"
        else:
            continue
        why = f"flow {fl['id']}" if hit else f"critical flow {fl['id']}"
        for r in rows:
            if fl["id"] in r.get("flows", []):
                floor.add(r, why, hit)

    for r in rows:
        if r["covers"]:
            src = next((f for f in files if globs.match_any(f, r["covers"])), None)
            if src:
                floor.add(r, f"covers {src}", src)

    missing: list[dict[str, Any]] = []
    existing = set(list_files(cfg.root))
    changed = {f["path"]: f for f in changeset["files"]}
    for f in src_files:
        if changed[f]["status"] == "D":
            continue
        mirrors = [(s, m) for s in cfg.suites for m in s.get("mirror", []) if mirror_test(f, m)]
        # An overlay source file is mirrored by the overlay suite, not by the base suite whose src root also contains it.
        same = [(s, m) for s, m in mirrors if s.get("edition", "oss") == changed[f]["edition"]]
        # Other suites are a fallback: the Node API keeps overlay tests in two directories.
        if any(_mirror_hit(f, changed[f]["edition"], s, m, by_path, by_suite, floor) for s, m in same + [x for x in mirrors if x not in same]):
            continue
        for s, mirror in same or mirrors:
            test = mirror_test(f, mirror)
            test_dir = posixpath.dirname(test) + "/"
            # Neighbours are a safety net, not a mapping: the file stays "unmapped".
            # Only those named like the file; a whole directory defeats selection.
            area = _name_root(f)
            for r in by_suite.get(s["id"], []):
                if r["path"].startswith(test_dir) and area in posixpath.basename(r["path"]):
                    floor.add(r, f"same area as {f}")
            if test not in existing and not NO_UNIT_TEST.search(f):
                missing.append({
                    "source": f, "level": "unit", "suggested_path": test,
                    "why": "no unit test mirrors this file", "origin": "rules",
                })

    depth = int(prof.get("import_depth", 2))
    _import_graph_tests(cfg, files, rows, by_path, floor, depth)

    for a in cfg.always.get("always", []):
        if any(globs.match_any(f, a.get("when", ["**"])) for f in files):
            for r in by_suite.get(a["suite"], []):
                tag = a.get("grep")
                if tag and tag not in r.get("tags", []):
                    continue
                floor.add(r, f"always: {a['id']}", grep=tag)
    for test_id in history.get("failing_on_main", []):
        if test_id in by_id:
            floor.add(by_id[test_id], "failing on main")

    missing += _structural_gaps(cfg, changeset, existing)

    deferred = []
    if not prof.get("integration", True):
        for test_id in list(floor.items):
            if by_id[test_id].get("family") == "it-pytest" and "service:stack" in by_id[test_id]["needs"]:
                deferred.append(test_id)
                del floor.items[test_id]

    for test_id in floor.items:
        r = by_id[test_id]
        if r["family"] == "it-pytest" and "requires_kafka" in r["markers"]:
            stack["broker"] = "kafka"

    unmapped = [
        f for f in src_files
        if floor.by_file.get(f, 0) == 0 and changed[f]["kind"] == "src" and CODE.search(f)
    ]
    selected = set(floor.items)
    candidates = _candidates(rows, files, selected, in_scope)
    planner_name = prof.get("planner", "default")
    planner_reason = None
    if planner_name == "none":
        planner_reason = "profile planner is none"
    elif all(f["kind"] == "docs" and not f["path"].startswith(PRODUCT_DIRS) for f in changeset["files"]):
        planner_name, planner_reason = "none", "no testable change"
    elif {s["id"] for s in cfg.suites if scope == "all" or s.get("edition", "oss") == scope} <= set(full_suites):
        planner_name, planner_reason = "none", "full run"
    return {
        "schema": "qa-plan-input/v1",
        "mode": changeset["mode"],
        "edition": scope,
        "profile": profile,
        "notes": notes,
        "change": {
            "base": changeset["base"],
            "head": changeset["head"],
            "branch": changeset.get("branch"),
            "commits": changeset.get("commits", []),
            "files": changeset["files"],
            "diff_path": "changeset.diff",
        },
        "floor": {"tests": sorted(floor.items), "items": floor.items},
        "full_suites": full_suites,
        "matched_rules": matched_rules,
        "flows": flows,
        "deferred": sorted(deferred),
        "stack": stack,
        "matrix": sorted(matrix),
        "unmapped": sorted(set(unmapped)),
        "missing": missing,
        "candidates": candidates,
        "catalogs": [
            f"{s['catalog']}/tests.jsonl" for s in cfg.suites
            if s.get("catalog") and (cfg.root / s["catalog"] / "tests.jsonl").is_file()
        ],
        "history": {"failing_on_main": history.get("failing_on_main", [])},
        "budget": {"extra_minutes": prof.get("extra_minutes", 0)},
        "planner": planner_name,
        "planner_reason": planner_reason,
    }


def _import_graph_tests(cfg, files, rows, by_path, floor: Floor, depth: int) -> None:
    graph_suites = {s["id"]: s.get("import_graph") for s in cfg.suites if s.get("import_graph")}
    py_changed = {m: f for f in files if (m := graphs.py_module(f))}
    if py_changed and "python" in graph_suites.values():
        g, _ = graphs.python_graph(cfg.root, list_files(cfg.root))
        for mod, src in py_changed.items():
            for node, hops in g.dependents([mod], depth).items():
                if node.endswith(".py"):
                    for r in by_path.get(node, []):
                        if graph_suites.get(r["suite"]) == "python":
                            floor.add(r, _hops(src, hops), src)
    ts_changed = [f for f in files if f.startswith(graphs.TS_ROOTS) and f.endswith((".ts", ".tsx"))]
    if ts_changed and "typescript" in graph_suites.values():
        g = graphs.typescript_graph(cfg.root, list_files(cfg.root))
        for src in ts_changed:
            for node, hops in g.dependents([src], depth).items():
                if hops == 0:
                    continue
                for r in by_path.get(node, []):
                    if graph_suites.get(r["suite"]) == "typescript":
                        floor.add(r, _hops(src, hops), src)


def _hops(src: str, hops: int) -> str:
    return f"imports {src} ({hops} hop{'s' if hops > 1 else ''})"


def _structural_gaps(cfg: Config, changeset: dict[str, Any], existing: set[str]) -> list[dict[str, Any]]:
    gaps = []
    added = [f["path"] for f in changeset["files"] if f["status"] == "A"]
    seen = set()
    for path in added:
        parts = path.split("/")
        if path.startswith("backend/python/app/connectors/sources/") and len(parts) > 6:
            name = parts[5]
            test_dir = f"integration-tests/connectors/{name}/"
            if name not in seen and not any(f.startswith(test_dir) for f in existing):
                seen.add(name)
                gaps.append({
                    "source": f"backend/python/app/connectors/sources/{name}/", "level": "integration",
                    "suggested_path": test_dir, "origin": "rules",
                    "why": "new connector with no integration suite (scripts/connector_coverage.py)",
                })
        if path.endswith(".routes.ts"):
            gaps.append({
                "source": path, "level": "integration", "origin": "rules",
                "suggested_path": "integration-tests/response-validation/",
                "why": "new HTTP routes need a response-validation test",
            })
    return gaps


# Path words too common to say what a change is about.
GENERIC_WORDS = {"backend", "python", "nodejs", "apps", "src", "app", "api", "routes", "route", "modules",
                 "services", "service", "tests", "test", "unit", "integration", "utils", "core", "frontend",
                 "index", "init", "main", "response", "validation", "spec", "integration_test"}


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) >= 4 and w not in GENERIC_WORDS}


def _candidates(rows, files, selected, in_scope) -> list[dict[str, Any]]:
    """Catalog rows near the change that the floor did not pick, for the model.

    Ranked by how many words they share with the changed paths, so the cut keeps
    the related tests: a model without file access sees only this list."""
    areas = {"/".join(f.split("/")[:4]) for f in files}
    changed = set().union(*(_words(f) for f in files)) if files else set()
    scored = []
    for r in rows:
        if r["id"] in selected or not in_scope(r) or r.get("status") == "planned":
            continue
        near = any(r["path"].startswith(a) for a in areas)
        text = " ".join([r["id"], r.get("title") or "", *(r.get("covers") or []), *(r.get("markers") or [])])
        score = len(changed & _words(text)) + (2 if near else 0)
        if score > 0:
            scored.append((score, {k: r.get(k) for k in ("id", "tc", "title", "markers", "covers", "family")}))
    scored.sort(key=lambda sc: (-sc[0], sc[1]["family"] not in ("it-pytest", "playwright"), sc[1]["id"]))
    return [c for _, c in scored[:MAX_CANDIDATES]]


def summarize(plan_input: dict[str, Any]) -> Iterable[str]:
    yield f"profile {plan_input['profile']}, floor {len(plan_input['floor']['tests'])} tests"
    if plan_input["full_suites"]:
        yield "full suites: " + ", ".join(sorted(plan_input["full_suites"]))
    if plan_input["unmapped"]:
        yield f"{len(plan_input['unmapped'])} changed files map to no test"

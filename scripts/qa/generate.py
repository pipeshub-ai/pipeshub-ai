"""Propose planned scenarios for one module and write them as stub tests, then
implement approved stubs.

The model only returns JSON. The engine writes one stub file per scenario, and
only into a suite with `planned = true`. Model text goes into a docstring only
through doc_safe, and a stub that does not read back as the same planned row
is deleted.

Stage 2 (implement) runs only for a PR with the label qa-scenarios-ok and a
committed, clean stub. The reply replaces that one stub file and must pass
check_content: it may hold only imports, assignments, a docstring and the stub's
own test function at module level, and it may not import or name the modules
and builtins that reach the file system, processes, the network or dynamic code.
Anything else is rejected, not trimmed. That check is a best-effort denylist,
not a sandbox. A test that is not proven is restored from git (a failed restore
is reported and fails the command); a proven one stays uncommitted for a person
to review.
"""

from __future__ import annotations

import ast
import json
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Any

from qa import catalog, config, globs, paths, planner, prove
from qa.config import Config, list_files
from qa.impact import mirror_test
from qa.validate import clean_text

MAX_SCENARIOS, MAX_SOURCES, MAX_TITLE = 20, 200, 120
NEXT = "next: open a PR; a reviewer adds the label qa-scenarios-ok"
APPROVAL_LABEL = "qa-scenarios-ok"
MAX_CONTENT, MAX_NEIGHBOUR, MAX_SOURCES_TEXT = 30_000, 20_000, 40_000
# shortcut: a best-effort denylist of names, not a sandbox; code that reaches these through an allowed
# module's attributes still gets through. The label, the proof and the human review of the uncommitted
# file are the real gate. Run generated tests in a container if that ever stops being enough.
BANNED_IMPORTS = {"os", "subprocess", "socket", "sys", "shutil", "pathlib", "importlib", "ctypes", "builtins",
                  "urllib", "http", "requests", "httpx", "aiohttp", "multiprocessing", "pty", "signal", "tempfile",
                  "socketserver", "ftplib", "smtplib", "telnetlib", "pickle", "marshal"}
BANNED_NAMES = {"__import__", "eval", "exec", "compile", "open", "getattr", "setattr", "delattr", "globals", "locals",
                "vars", "__builtins__", "builtins", "importlib", "ctypes", "breakpoint", "input"}
RESTORE_FAILED = "restore failed: "
IMPLEMENT_NEXT = "next: review the kept tests with git diff and commit them yourself; nothing was committed"

UNIT_STUB = '''"""{tc}: {title}
{doc}
"""

import pytest

pytestmark = [pytest.mark.planned]


def test_{slug}() -> None:
    ...
'''

IT_STUB = '''"""Planned scenario for {module}, proposed by qa generate."""

import pytest


@pytest.mark.planned
@pytest.mark.integration
def test_{slug}() -> None:
    """{tc}: {title}
{doc}
    """
    ...
'''


def tc_area(module_id: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", module_id.upper()).strip("-")


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:50]


def doc_safe(text: str, limit: int = 200) -> str:
    """One line that cannot close a docstring: no quotes, no backslashes, no newlines."""
    return clean_text(text, limit).replace('"', "'").replace("\\", "/")


def _module(cfg: Config, module_id: str) -> dict[str, Any] | None:
    return next((m for m in cfg.modules if m["id"] == module_id), None)


def _in_module(module: dict[str, Any], path: str) -> bool:
    return globs.match_any(path, module.get("paths", []))


def scenario_input(cfg: Config, rows: list[dict], module_id: str, plan: dict | None, max_new: int) -> dict:
    module = _module(cfg, module_id)
    assert module is not None, module_id
    sources = [f for f in list_files(cfg.root) if _in_module(module, f) and catalog.suite_of(f, cfg) is None]
    out = {
        "module": {"id": module["id"], "title": module.get("title", module["id"]), "paths": module.get("paths", [])},
        "flows": [{"id": f["id"], "title": f.get("title", f["id"]), "priority": f.get("priority")}
                  for f in cfg.flows if module_id in f.get("modules", [])],
        "existing": [{"id": r["id"], "title": r["title"], "tc": r["tc"], "status": r["status"]}
                     for r in rows if r.get("module") == module_id],
        "sources": sources[:MAX_SOURCES],
        "max": max_new,
    }
    if plan is not None:
        out["missing"] = [m for m in plan.get("missing", []) if _in_module(module, m.get("source") or "")]
    return out


def _edition(cfg: Config, path: str) -> str:
    ed = cfg.edition(path)
    return paths.BASE_EDITION if ed == "switch" else ed


def _owned(cfg: Config, path: str, family: str, covers: list[str]) -> tuple[str | None, str]:
    """The stub path if a planned suite of this family owns it in the covers file's edition."""
    suite = catalog.suite_of(path, cfg)
    if not suite or not suite.get("planned") or suite["family"] != family:
        return None, f"no planned suite owns {path}"
    if covers and _edition(cfg, covers[0]) != suite.get("edition", paths.BASE_EDITION):
        return None, f"suite {suite['id']} is not in the edition of {covers[0]}"
    return path, f"suite {suite['id']}"


def home(cfg: Config, rows: list[dict], module: dict, scenario: dict) -> tuple[str | None, str]:
    name = f"test_{slug(scenario['title'])}.py"
    covers = scenario.get("covers", [])
    if scenario["level"] == "unit":
        source = next((c for c in covers if c.endswith(".py")), None)
        if not source:
            return None, "no Python file in covers"
        for s in cfg.suites:
            if not s.get("planned") or s["family"] != "pytest":
                continue
            for m in s.get("mirror", []):
                if m.get("style") == "py" and (test := mirror_test(source, m)):
                    path, why = _owned(cfg, posixpath.join(posixpath.dirname(test), name), "pytest", covers)
                    if path:
                        return path, why
        return None, f"no planned unit suite mirrors {source}"
    if scenario["level"] == "integration":
        planned = {s["id"] for s in cfg.suites if s.get("planned") and s["family"] == "it-pytest"}
        flows = {f["id"] for f in cfg.flows if module["id"] in f.get("modules", [])}
        mine = [r for r in rows if r["suite"] in planned]
        row = (next((r for r in mine if r.get("module") == module["id"]), None)
               or next((r for r in mine if flows & set(r.get("flows", []))), None))
        d = posixpath.dirname(row["path"]) if row else f"integration-tests/{module['id'].replace('-', '_')}"
        return _owned(cfg, posixpath.join(d, name), "it-pytest", covers)
    return None, f"level {scenario['level']} has no stub suite"


def _next_tc(root: Path, directory: str, area: str) -> str:
    pattern = re.compile(rf"\bTC-{re.escape(area)}-(\d{{3}})\b")
    d = root / directory
    found = [int(n) for p in (d.glob("*.py") if d.is_dir() else []) for n in pattern.findall(p.read_text(encoding="utf-8", errors="replace"))]
    return f"TC-{area}-{max(found, default=0) + 1:03d}"


def plain_path(path: str) -> bool:
    """A relative repo path with no traversal, glob or backslash: string matching on it means what it says."""
    return (not path.startswith("/") and not any(ch in path for ch in "*?\\")
            and ".." not in path.split("/") and path == posixpath.normpath(path)
            and bool(catalog.PATHISH.fullmatch(path)))


def _inside_suite(cfg: Config, path: str) -> bool:
    """The resolved target lies under the static directory of one of its planned suite's test globs."""
    suite = catalog.suite_of(path, cfg)
    if not suite or not suite.get("planned"):
        return False
    target = (cfg.root / path).resolve()
    for pattern in suite.get("tests", []):
        top = pattern.split("*", 1)[0].rsplit("/", 1)[0]
        if top and target.is_relative_to((cfg.root / top).resolve()):
            return True
    return False


def _free(root: Path, path: str) -> str:
    stem, n = path.removesuffix(".py"), 1
    while (root / path).exists():
        n += 1
        path = f"{stem}_{n}.py"
    return path


def _doc_lines(scenario: dict, indent: str) -> str:
    lines = [f"steps: {doc_safe(s)}" for s in scenario.get("steps", [])[:8]]
    lines += [f"expected: {doc_safe(s)}" for s in scenario.get("expected", [])[:5]]
    lines += [f"data: {doc_safe(s)}" for s in scenario.get("data", [])[:5]]
    if scenario["covers"]:
        lines.append("covers: " + ", ".join(scenario["covers"]))
    return "\n".join(indent + line for line in lines)


def write_scenarios(cfg: Config, rows: list[dict], module_id: str, llm: dict) -> dict:
    module = _module(cfg, module_id)
    assert module is not None, module_id
    seen = {(r.get("title") or "").lower() for r in rows if r.get("module") == module_id or r.get("status") == "planned"}
    written: list[dict] = []
    dropped: list[dict] = []
    for raw in llm.get("scenarios", [])[:MAX_SCENARIOS]:
        title = doc_safe(raw.get("title"), MAX_TITLE)
        level = raw.get("level")
        if not slug(title):
            dropped.append({"title": title, "why": "empty title"})
            continue
        if level == "e2e":
            dropped.append({"title": title, "why": "e2e stubs are not supported yet"})
            continue
        asked = [c for c in raw.get("covers", []) if isinstance(c, str)]
        # Covers pick the stub's directory and go into its docstring, so only plain module paths survive.
        covers = [c for c in asked if plain_path(c) and _in_module(module, c)]
        if asked and not covers:
            dropped.append({"title": title, "why": "covers outside the module"})
            continue
        if title.lower() in seen:
            dropped.append({"title": title, "why": "duplicate title"})
            continue
        sc = {**raw, "title": title, "level": level, "covers": covers}
        target, why = home(cfg, rows, module, sc)
        if not target:
            dropped.append({"title": title, "why": f"no {clean_text(level, 20)} home: {why}"})
            continue
        path = _free(cfg.root, target)
        tc = _next_tc(cfg.root, posixpath.dirname(path), tc_area(module_id))
        values = {"tc": tc, "title": title, "slug": slug(title), "module": doc_safe(module.get("title", module_id))}
        if level == "unit":
            text = UNIT_STUB.format(doc=_doc_lines(sc, ""), **values)
        else:
            text = IT_STUB.format(doc=_doc_lines(sc, "    "), **values)
        if not _inside_suite(cfg, path):
            dropped.append({"title": title, "why": "unsafe path"})
            continue
        full = cfg.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "x", encoding="utf-8") as f:
            f.write(text)
        back = catalog.read_rows(cfg, path)
        if not (len(back) == 1 and back[0]["status"] == "planned" and back[0]["title"] == title and back[0]["tc"] == tc):
            full.unlink()  # fail closed: a stub the catalog cannot read as this planned scenario is not kept
            dropped.append({"title": title, "why": "stub did not parse as planned"})
            continue
        seen.add(title.lower())
        written.append({"id": back[0]["id"], "path": path, "tc": tc, "title": title})
    return {"written": written, "dropped": dropped}


def render(result: dict, module_id: str, status: dict) -> str:
    lines = [f"# qa generate: {clean_text(module_id, 60)}", "",
             f"Model: {clean_text(status.get('adapter'), 60)}, {clean_text(status.get('status'), 60)}"
             + (f" ({clean_text(status['error'])})" if status.get("error") else ""), "", "## Written", ""]
    lines += [f"- {w['tc']}: {w['title']} ({w['path']})" for w in result["written"]] or ["None."]
    lines += ["", "## Dropped", ""]
    lines += [f"- {d['title']}: {d['why']}" for d in result["dropped"]] or ["None."]
    return "\n".join(lines + ["", NEXT]) + "\n"


# --- stage 2: implement ---------------------------------------------------------------

def approved(pr: int, repo: str | None) -> bool:
    """The PR carries APPROVAL_LABEL, read with gh. Any failure means not approved."""
    cmd = ["gh", "pr", "view", str(pr), "--json", "labels"] + (["--repo", repo] if repo else [])
    try:
        data = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60).stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    labels = data.get("labels") if isinstance(data, dict) else None
    return any(isinstance(lb, dict) and lb.get("name") == APPROVAL_LABEL for lb in labels or [])


def _test_name(stub: str) -> str | None:
    try:
        tree = ast.parse(stub)
    except SyntaxError:
        return None
    return next((n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test")), None)


def implement_input(cfg: Config, row: dict) -> dict:
    stub = (cfg.root / row["path"]).read_text(encoding="utf-8")
    directory = posixpath.dirname(row["path"])
    siblings = sorted({r["path"] for r in catalog.build(cfg) if r.get("status") == "active"
                       and r["path"] != row["path"] and posixpath.dirname(r["path"]) == directory})
    neighbours = [{"path": p, "text": (cfg.root / p).read_text(encoding="utf-8", errors="replace")[:MAX_NEIGHBOUR]}
                  for p in siblings[:2]]
    sources, room = [], MAX_SOURCES_TEXT
    for c in row.get("covers", []):
        if room > 0 and c.endswith(".py") and plain_path(c) and (cfg.root / c).is_file():
            text = (cfg.root / c).read_text(encoding="utf-8", errors="replace")[:room]
            room -= len(text)
            sources.append({"path": c, "text": text})
    keys = ("id", "title", "tc", "steps", "expected", "data", "covers", "path")
    return {"row": {k: row.get(k) for k in keys}, "stub": stub, "neighbours": neighbours, "sources": sources}


def _module_level_ok(node: ast.stmt, name: str) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
        return True
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name


def check_content(stub: str, content: str) -> list[str]:
    """Why a model reply may not replace the stub; empty means it may (then prove decides)."""
    problems = []
    if len(content.encode("utf-8")) > MAX_CONTENT:
        problems.append(f"larger than {MAX_CONTENT} bytes")
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return problems + [f"does not parse: {exc.msg} (line {exc.lineno})"]
    name = _test_name(stub) or "test function"
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in tree.body):
        problems.append(f"missing {name}")
    if any(isinstance(n, ast.Attribute) and n.attr == "planned" for n in ast.walk(tree)):
        problems.append("still marked planned")
    for n in ast.walk(tree):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
        problems += [f"imports {m.split('.')[0]} (line {n.lineno})" for m in mods if m.split(".")[0] in BANNED_IMPORTS]
        ident = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
        if ident and (ident in BANNED_NAMES and isinstance(n, ast.Name)
                      or ident.startswith("__") and ident.endswith("__") and ident != "__name__"):
            problems.append(f"uses {ident} (line {n.lineno})")
    seen = False
    for n in tree.body:
        mine = isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        if not _module_level_ok(n, name) or (mine and seen):
            problems.append(f"only imports, assignments and {name} may be at module level: {type(n).__name__} at line {n.lineno}")
        seen = seen or mine
    return problems + prove.assertion_problems(content, name)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def _refusal(cfg: Config, row: dict | None) -> str | None:
    if row is None:
        return "not in the catalog"
    if row.get("status") != "planned":
        return f"status is {row.get('status')}, not planned"
    path = row["path"]
    if not plain_path(path) or not _inside_suite(cfg, path) or not (cfg.root / path).is_file():
        return "unsafe path"
    if _git(cfg.root, "ls-files", "--error-unmatch", "--", path).returncode:
        return "the stub is not committed"
    if _git(cfg.root, "status", "--porcelain", "--", path).stdout.strip():
        return "the stub has uncommitted changes"
    return None


def implement(cfg: Config, rows: list[dict], ids: list[str], out: Path, adapter: str, timeout: int,
              prove_kwargs: dict) -> dict:
    index = catalog.index(rows)
    result: dict[str, list[dict]] = {"kept": [], "removed": [], "refused": []}
    work = out / "implement"
    work.mkdir(parents=True, exist_ok=True)
    for test_id in ids:
        row = index.get(test_id)
        why = _refusal(cfg, row)
        if why:
            result["refused"].append({"id": test_id, "reason": why})
            continue
        path = cfg.root / row["path"]
        stub = path.read_text(encoding="utf-8")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", test_id)
        (work / f"{safe}-input.json").write_text(json.dumps(implement_input(cfg, row), indent=2), encoding="utf-8")
        status = planner.ask(cfg.root, adapter, "implement", work / f"{safe}-input.json", work / f"{safe}-llm.json", timeout=timeout)
        content = json.loads((work / f"{safe}-llm.json").read_text(encoding="utf-8")).get("content") or ""
        if not content.strip():
            result["removed"].append({"id": test_id, "reason": f"no test from the model ({status.get('error') or status.get('status')})"})
            continue
        problems = check_content(stub, content)
        if problems:
            result["removed"].append({"id": test_id, "reason": "; ".join(problems)})
            continue
        kept = False
        try:
            # The only file the engine writes: the stub's own tracked path, checked by _refusal above.
            path.write_text(content, encoding="utf-8")
            verdict = prove.prove(cfg, row, out, **prove_kwargs)
            kept = verdict.get("verdict") == "proven"
            result["kept" if kept else "removed"].append({"id": test_id, "reason": verdict.get("reason", "")})
        finally:
            # fail closed, also when prove crashes: the committed stub comes back byte for byte, or we say it did not
            restored = kept or _git(cfg.root, "checkout", "--", row["path"])
            if not kept and restored.returncode:
                line = (restored.stderr.strip().splitlines() or ["git checkout failed"])[0]
                result["removed"] = [e for e in result["removed"] if e["id"] != test_id]
                result["removed"].append({"id": test_id, "reason": RESTORE_FAILED + line})
    config.clear_cache()  # the catalog must read the kept files as they are now
    catalog.write(cfg)
    (out / "implement.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    (out / "generate.md").write_text(render_implement(result), encoding="utf-8")
    return result


def render_implement(result: dict) -> str:
    lines = ["# qa generate --implement", ""]
    for key, title in (("kept", "Kept (proven, uncommitted)"), ("removed", "Removed (stub restored)"), ("refused", "Refused")):
        lines += [f"## {title}", ""]
        lines += [f"- {clean_text(e['id'], 200)}: {clean_text(e['reason'], 500)}" for e in result[key]] or ["None."]
        lines.append("")
    return "\n".join(lines + [IMPLEMENT_NEXT]) + "\n"

"""Build the test catalog: every test that exists, with the spec it declares.

The spec lives in the test. The first docstring line is
`TC-<AREA>-<NNN>: <plain English>`, optionally followed by `covers:` and
`needs:` lines. This module reads it with `ast` (never importing a test) and
writes it as JSON lines that any model or person can grep.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qa import globs, paths, pyparse
from qa.config import Config, list_files

PREFIX = {
    "pytest": "py",
    "it-pytest": "it",
    "mocha": "node",
    "vitest": "fe",
    "playwright": "e2e",
    "unittest": "scripts",
    "npm-script": "suite",
}

TC_LINE = re.compile(r"^\s*(TC-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3})\s*:\s*(.+?)\s*$")
TAG_LINE = re.compile(r"^\s*(covers|needs|steps|expected|data)\s*:\s*(.+?)\s*$", re.IGNORECASE)
PATHISH = re.compile(r"^[\w.*?/-]+/[\w.*?/-]*$")

# Plugin and execution markers that say nothing about what a test covers.
GENERIC_MARKERS = {
    "asyncio", "order", "parametrize", "usefixtures", "timeout", "filterwarnings",
    "skip", "skipif", "xfail", "xdist_group", "flaky", "dependency",
}

TS_TEST = re.compile(r"""\b(?:it|test)(?:\.only|\.skip)?\(\s*(['"`])(.+?)\1""")
TS_DESCRIBE = re.compile(r"""\bdescribe(?:\.only|\.skip)?\(\s*(['"`])(.+?)\1""")
TS_TAG = re.compile(r"""@(?:tc|covers|needs)\s+(.+)""")
E2E_TAG = re.compile(r"@[a-z][a-z0-9_-]*")


@dataclass
class Spec:
    tc: str | None
    title: str
    covers: list[str]
    needs: list[str]
    steps: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)


def parse_docstring(doc: str | None) -> Spec:
    if not doc:
        return Spec(None, "", [], [])
    lines = doc.strip().splitlines()
    first = lines[0].strip()
    m = TC_LINE.match(first)
    tc, title = (m.group(1), m.group(2)) if m else (None, first)
    covers: list[str] = []
    needs: list[str] = []
    prose: dict[str, list[str]] = {"steps": [], "expected": [], "data": []}
    for line in lines[1:]:
        t = TAG_LINE.match(line)
        if not t:
            continue
        if t.group(1).lower() in prose:
            prose[t.group(1).lower()].append(t.group(2))
            continue
        items = [i.strip() for i in t.group(2).split(",") if i.strip()]
        # Prose like "covers: the retry path" is not a tag: values must look like paths / kinds.
        if t.group(1).lower() == "covers" and all(PATHISH.match(i) for i in items):
            covers.extend(items)
        elif t.group(1).lower() == "needs" and all(":" in i for i in items):
            needs.extend(items)
    return Spec(tc, title, covers, needs, **prose)


def suite_of(path: str, cfg: Config) -> dict[str, Any] | None:
    """The suite owning a test file: the edition-matching one wins a tie."""
    matches = [
        s for s in cfg.suites
        if globs.match_any(path, s.get("tests", [])) and not globs.match_any(path, s.get("exclude", []))
    ]
    if not matches:
        return None
    ed = cfg.edition(path)
    preferred = [s for s in matches if s.get("edition", "oss") == ed]
    return (preferred or matches)[0]


def rel_to(path: str, cwd: str) -> str:
    if cwd in ("", "."):
        return path
    return path[len(cwd) + 1:] if path.startswith(cwd + "/") else path


def _mark_names(node: ast.AST) -> list[str]:
    out = []
    for sub in ast.walk(node):
        target = sub.func if isinstance(sub, ast.Call) else sub
        if (
            isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark" and isinstance(target.value.value, ast.Name) and target.value.value.id == "pytest"
        ):
            out.append(target.attr)
    return out


def _module_marks(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            return _mark_names(node.value)
    return []


def _secrets_in(source: str) -> list[str]:
    """Env vars a conftest names via `secrets=[...]` when a credential is missing."""
    try:
        tree = pyparse.parse(source)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "secrets" and isinstance(node.value, (ast.List, ast.Tuple)):
            out.extend(e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return out


def _is_fixture(fn: ast.AST) -> bool:
    for d in getattr(fn, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        if (isinstance(target, ast.Name) and target.id == "fixture") or (isinstance(target, ast.Attribute) and target.attr == "fixture"):
            return True
    return False


def _autouse(fn: ast.AST) -> bool:
    return any(
        isinstance(d, ast.Call) and any(k.arg == "autouse" and isinstance(k.value, ast.Constant) and k.value.value is True for k in d.keywords)
        for d in fn.decorator_list
    )


def _params(fn: ast.AST) -> list[str]:
    a = fn.args
    return [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs if x.arg not in ("self", "cls", "request")]


def _called(fn: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            out.add(n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
    return out


@dataclass
class _Fixture:
    params: list[str]
    autouse: bool
    needs: set[str]


def _fixtures(tree: ast.Module, triggers: dict[str, str]) -> dict[str, _Fixture]:
    """Fixtures a module defines, with the needs of any trigger function they call."""
    out: dict[str, _Fixture] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_fixture(node):
            out[node.name] = _Fixture(_params(node), _autouse(node), {triggers[c] for c in _called(node) if c in triggers})
    return out


def _usefixtures(node: ast.AST) -> list[str]:
    out = []
    for n in ast.walk(ast.Module(body=list(getattr(node, "decorator_list", [])), type_ignores=[])):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "usefixtures":
            out.extend(a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return out


class _ConftestNeeds:
    """Needs a test inherits from conftest.py files: credentials they name, and
    the needs of every fixture the test reaches (requested, autouse or usefixtures)."""

    def __init__(self, root: Path, top: str, triggers: dict[str, str] | None = None):
        self.root = root
        self.top = top
        self.triggers = triggers or {}
        self.cache: dict[str, tuple[list[str], dict[str, _Fixture]]] = {}
        self.module: tuple[ast.Module | None, dict[str, _Fixture]] = (None, {})

    def _chain(self, path: str) -> list[str]:
        out = []
        d = str(Path(path).parent)
        while True:
            out.append(d)
            if d in (self.top, ".", "") or not d.startswith(self.top):
                return out
            d = str(Path(d).parent)

    def for_file(self, path: str) -> list[str]:
        return sorted({s for d in self._chain(path) for s in self._dir(d)[0]})

    def for_test(self, path: str, tree: ast.Module, node: ast.AST, cls: ast.AST | None = None) -> list[str]:
        if not self.triggers:
            return []
        # Nearest definition wins, as in pytest; a fixture sees only its own level and above.
        if self.module[0] is not tree:
            self.module = (tree, _fixtures(tree, self.triggers))
        levels = [self.module[1]] + [self._dir(d)[1] for d in self._chain(path)]
        memo: dict[tuple[str, int], set[str]] = {}

        def resolve(name: str, start: int) -> set[str]:
            for i in range(start, len(levels)):
                fx = levels[i].get(name)
                if fx is None:
                    continue
                if (name, i) not in memo:
                    memo[(name, i)] = set()
                    memo[(name, i)] = set(fx.needs).union(*(resolve(p, i + 1 if p == name else i) for p in fx.params))
                return memo[(name, i)]
            return set()

        names = _params(node) + _usefixtures(node) + (_usefixtures(cls) if cls else [])
        names += [n for level in levels for n, fx in level.items() if fx.autouse]
        return sorted(set().union(*(resolve(n, 0) for n in names)))

    def _dir(self, d: str) -> tuple[list[str], dict[str, _Fixture]]:
        if d not in self.cache:
            f = self.root / d / "conftest.py"
            if not f.is_file():
                self.cache[d] = ([], {})
            else:
                source = f.read_text(encoding="utf-8", errors="replace")
                try:
                    fixtures = _fixtures(pyparse.parse(source), self.triggers)
                except SyntaxError:
                    fixtures = {}
                self.cache[d] = ([f"env:{s}" for s in _secrets_in(source)], fixtures)
        return self.cache[d]


def _python_rows(cfg: Config, suite: dict[str, Any], path: str, needs: _ConftestNeeds | None) -> list[dict[str, Any]]:
    source = (cfg.root / path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = pyparse.parse(source)
    except SyntaxError:
        return [_row(cfg, suite, path, None, Spec(None, "unparsable test file", [], []), [], {"parse_error": True})]
    module_marks = _module_marks(tree)
    ordered = "order" in module_marks
    tests: list[tuple[str, ast.AST, list[str], ast.AST | None]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests.append((node.name, node, _mark_names(ast.Module(body=node.decorator_list, type_ignores=[])), None))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            class_marks = _mark_names(ast.Module(body=node.decorator_list, type_ignores=[]))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    marks = class_marks + _mark_names(ast.Module(body=item.decorator_list, type_ignores=[]))
                    tests.append((f"{node.name}::{item.name}", item, marks, node))
    ordered = ordered or any("order" in m for _, _, m, _ in tests)
    extra_needs = needs.for_file(path) if needs else []
    test_needs = {name: needs.for_test(path, tree, node, cls) if needs else [] for name, node, _, cls in tests}

    if suite.get("grain") == "test":
        rows = []
        for name, node, marks, _ in tests:
            spec = parse_docstring(ast.get_docstring(node))
            if not spec.title:
                spec.title = name.rsplit("::", 1)[-1].removeprefix("test_").replace("_", " ")
            all_marks = sorted({m for m in module_marks + marks if m not in GENERIC_MARKERS})
            rows.append(_row(cfg, suite, path, name, spec, all_marks, {"ordered": ordered}, extra_needs + test_needs[name]))
        return rows

    spec = parse_docstring(ast.get_docstring(tree))
    marks = sorted({m for m in module_marks if m not in GENERIC_MARKERS})
    file_needs = extra_needs + [n for ns in test_needs.values() for n in ns]
    return [_row(cfg, suite, path, None, spec, marks, {"tests": len(tests)}, file_needs)]


def _ts_rows(cfg: Config, suite: dict[str, Any], path: str) -> list[dict[str, Any]]:
    source = (cfg.root / path).read_text(encoding="utf-8", errors="replace")
    titles = [m.group(2) for m in TS_TEST.finditer(source)]
    describe = TS_DESCRIBE.search(source)
    covers: list[str] = []
    needs: list[str] = []
    tc = None
    for m in TS_TAG.finditer(source):
        key = m.group(0).split()[0][1:]
        value = m.group(1).strip().rstrip("*/").strip()
        if key == "tc":
            tc = value.split(":")[0].strip()
        elif key == "covers":
            covers.extend(v.strip() for v in value.split(","))
        else:
            needs.extend(v.strip() for v in value.split(","))
    spec = Spec(tc, describe.group(2) if describe else (titles[0] if titles else ""), covers, needs)
    extra: dict[str, Any] = {"tests": len(titles)}
    if suite["family"] == "playwright":
        extra["tags"] = sorted({t for title in titles for t in E2E_TAG.findall(title)})
    return [_row(cfg, suite, path, None, spec, [], extra)]


def _row(
    cfg: Config, suite: dict[str, Any], path: str, name: str | None, spec: Spec,
    markers: list[str], extra: dict[str, Any], extra_needs: Iterable[str] = (),
) -> dict[str, Any]:
    rel = rel_to(path, suite.get("cwd", "."))
    ident = f"{PREFIX[suite['family']]}:{rel}" + (f"::{name}" if name else "")
    by_marker = {n for m in markers if (n := suite.get("marker_needs", {}).get(m))}
    needs = sorted(set(spec.needs) | set(extra_needs) | by_marker | ({"service:stack"} if suite.get("stack") else set()))
    row = {
        "id": ident,
        "suite": suite["id"],
        "family": suite["family"],
        "edition": ed if (ed := cfg.edition(path)) != "switch" else paths.BASE_EDITION,
        "path": path,
        "tc": spec.tc,
        "title": spec.title,
        "markers": markers,
        "covers": spec.covers,
        "needs": needs,
        "status": "active",
    }
    row.update(extra)
    for k in ("steps", "expected", "data"):
        if getattr(spec, k):
            row[k] = getattr(spec, k)
    return row


def build(cfg: Config, suites: Iterable[str] | None = None) -> list[dict[str, Any]]:
    wanted = set(suites) if suites else None
    rows: list[dict[str, Any]] = []
    needs_cache: dict[str, _ConftestNeeds] = {}
    quarantined = cfg.quarantined_ids(auto=False)
    for path in list_files(cfg.root):
        suite = suite_of(path, cfg)
        if not suite or (wanted and suite["id"] not in wanted):
            continue
        fam = suite["family"]
        if fam in ("pytest", "it-pytest", "unittest"):
            needs = None
            if fam == "it-pytest":
                needs = needs_cache.setdefault(suite["cwd"], _ConftestNeeds(cfg.root, suite["cwd"], suite.get("fixture_needs")))
            new = _python_rows(cfg, suite, path, needs)
        else:
            new = _ts_rows(cfg, suite, path)
        for r in new:
            if _is_planned(cfg, r):
                r["status"] = "planned"
            else:
                for k in ("steps", "expected", "data"):
                    r.pop(k, None)
                if r["id"] in quarantined:
                    r["status"] = "quarantined"
        rows.extend(new)
    rows.sort(key=lambda r: r["id"])
    stamp(cfg, rows)
    return rows


def read_rows(cfg: Config, path: str) -> list[dict[str, Any]]:
    """The rows of one Python test file, with planned status, without building the whole catalog."""
    suite = suite_of(path, cfg)
    if not suite or suite["family"] not in ("pytest", "it-pytest", "unittest"):
        return []
    rows = _python_rows(cfg, suite, path, None)
    for r in rows:
        if _is_planned(cfg, r):
            r["status"] = "planned"
    return rows


def stamp(cfg: Config, rows: list[dict[str, Any]]) -> None:
    """Set module (first match in load order), flows (load order) and best priority on each row."""
    rank = ("P0", "P1", "P2").index
    for r in rows:
        r["module"] = next((m["id"] for m in cfg.modules if globs.match_any(r["path"], m.get("paths", []))), None)
        mine = [
            f for f in cfg.flows
            if globs.match_any(r["path"], f.get("tests", [])) or set(f.get("markers", [])) & set(r["markers"])
        ]
        r["flows"] = [f["id"] for f in mine]
        r["priority"] = min((f["priority"] for f in mine), key=rank, default=None)


def _is_planned(cfg: Config, row: dict[str, Any]) -> bool:
    return "planned" in row.get("markers", [])


def index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in rows}


# --- committed catalog files ------------------------------------------------

def committed_suites(cfg: Config) -> list[dict[str, Any]]:
    return [s for s in cfg.suites if s.get("catalog")]


def render_jsonl(rows: Iterable[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)


def render_markdown(suite: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    out = [
        f"# Test catalog: {suite['id']}",
        "",
        "Generated by `python3 scripts/qa catalog build`. Do not edit by hand:",
        "change the test's docstring and regenerate.",
        "",
        f"{len(rows)} tests; {sum(1 for r in rows if r['tc'])} with a TC id.",
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        rel = rel_to(r["path"], suite["cwd"])
        area = "/".join(rel.split("/")[:2]) if "/" in rel else rel
        groups.setdefault(area, []).append(r)
    for area in sorted(groups):
        out += ["", f"## {area}", "", "| TC | Test | What it checks | Markers | Needs |", "| --- | --- | --- | --- | --- |"]
        for r in groups[area]:
            name = r["id"].split("::", 1)[-1]
            title = (r["title"] or "").replace("|", "\\|")
            needs = ", ".join(n for n in r["needs"] if n != "service:stack")
            out.append(f"| {r['tc'] or ''} | `{name}` | {title} | {', '.join(r['markers'])} | {needs} |")
    return "\n".join(out) + "\n"


def _home(suites: list[dict[str, Any]], row: dict[str, Any]) -> str | None:
    """The committed suite holding a row: its own, or the same-family suite of the row's edition."""
    ed = lambda s: s.get("edition", paths.BASE_EDITION)
    own = next((s for s in suites if s["id"] == row["suite"]), None)
    if own and ed(own) == row["edition"]:
        return own["id"]
    found = next((s["id"] for s in suites if s["family"] == row["family"] and ed(s) == row["edition"]), None)
    # An overlay suite may hold base rows; any other edition without a suite has no home.
    return found or (own["id"] if own and row["edition"] == paths.BASE_EDITION else None)


def committed_outputs(cfg: Config, rows: list[dict[str, Any]] | None = None) -> dict[Path, str]:
    suites = committed_suites(cfg)
    rows = rows if rows is not None else build(cfg, [s["id"] for s in suites])
    out: dict[Path, str] = {}
    for s in suites:
        mine = [r for r in rows if _home(suites, r) == s["id"]]
        d = cfg.root / s["catalog"]
        out[d / "tests.jsonl"] = render_jsonl(mine)
        out[d / "CATALOG.md"] = render_markdown(s, mine)
    return out


def homeless(cfg: Config, rows: list[dict[str, Any]]) -> int:
    """Rows no committed catalog lists."""
    suites = committed_suites(cfg)
    return sum(_home(suites, r) is None for r in rows)


def write(cfg: Config, rows: list[dict[str, Any]] | None = None) -> list[Path]:
    written = []
    for path, text in committed_outputs(cfg, rows).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written


def stale(cfg: Config, rows: list[dict[str, Any]] | None = None) -> list[Path]:
    return [
        p for p, text in committed_outputs(cfg, rows).items()
        if not p.is_file() or p.read_text(encoding="utf-8") != text
    ]


def _unowned(cfg: Config) -> list[dict[str, Any]]:
    return cfg.always.get("unowned", [])


def orphans(cfg: Config) -> list[str]:
    """Test files no suite runs and no [[unowned]] entry says is run elsewhere."""
    tests = cfg.always.get("test_files", [])
    skip = [g for u in _unowned(cfg) for g in u.get("paths", [])]
    return [
        f for f in list_files(cfg.root)
        if globs.match_any(f, tests) and suite_of(f, cfg) is None and not globs.match_any(f, skip)
    ]


# --- lint -------------------------------------------------------------------

def lint(cfg: Config, rows: list[dict[str, Any]], strict: bool = False) -> tuple[list[str], list[str]]:
    """Errors block; warnings become errors under --strict (per migrated area)."""
    import datetime as dt

    errors: list[str] = []
    warnings: list[str] = []
    files = list_files(cfg.root)
    # TC ids name a scenario within an area (connectors reuse TC-SYNC-001 for
    # the same scenario), so they must be unique per directory, not globally.
    tcs = Counter((str(Path(r["path"]).parent), r["tc"]) for r in rows if r["tc"])
    for (area, tc), n in tcs.items():
        if n > 1:
            errors.append(f"duplicate TC id {tc} in {area} ({n} tests)")
    for r in rows:
        for pattern in r["covers"]:
            if not any(globs.match(f, pattern) for f in files):
                errors.append(f"{r['id']}: covers: {pattern!r} matches no file")
        if r["family"] == "it-pytest" and r["suite"] != "it-helpers" and not r["tc"]:
            warnings.append(f"{r['id']}: first docstring line has no TC id")
    module_ids = {m["id"] for m in cfg.modules}
    for m in cfg.modules:
        for pattern in m.get("paths", []):
            if not any(globs.match(f, pattern) for f in files):
                warnings.append(f"module {m['id']}: path {pattern!r} matches no file")
    for fl in cfg.flows:
        for mod in fl.get("modules", []):
            if mod not in module_ids:
                errors.append(f"flow {fl['id']}: unknown module {mod}")
        if fl.get("priority") not in ("P0", "P1", "P2"):
            errors.append(f"flow {fl['id']}: priority must be P0, P1 or P2")
        elif fl.get("critical") and fl["priority"] != "P0":
            errors.append(f"flow {fl['id']}: only a P0 flow can be critical")
        for pattern in fl.get("triggers", []):
            if not any(globs.match(f, pattern) for f in files):
                warnings.append(f"flow {fl['id']}: trigger {pattern!r} matches no file")
        for pattern in fl.get("tests", []):
            if not any(globs.match(r["path"], pattern) for r in rows):
                warnings.append(f"flow {fl['id']}: tests {pattern!r} matches no catalogued test")
    for rule in cfg.rules:
        if not rule.get("module"):
            errors.append(f"impact rule {rule['id']}: no module")
        elif rule["module"] not in module_ids:
            errors.append(f"impact rule {rule['id']}: unknown module {rule['module']}")
        for pattern in rule.get("paths", []):
            if not any(globs.match(f, pattern) for f in files):
                warnings.append(f"impact rule {rule['id']}: path {pattern!r} matches no file")
        for pattern in rule.get("tests", []):
            if not any(globs.match(r["path"], pattern) for r in rows):
                warnings.append(f"impact rule {rule['id']}: tests {pattern!r} matches no catalogued test")
    for path in orphans(cfg):
        errors.append(f"test file {path} belongs to no suite (add a suite or an [[unowned]] entry)")
    for u in _unowned(cfg):
        if not u.get("reason"):
            errors.append(f"unowned entry {u.get('paths', '?')} is missing a reason")
        for pattern in u.get("paths", []):
            if not any(globs.match(f, pattern) for f in files):
                warnings.append(f"unowned entry: path {pattern!r} matches no file")
    today = dt.date.today()
    for q in cfg.quarantine:
        missing = [k for k in ("id", "owner", "issue", "expires") if not q.get(k)]
        if missing:
            errors.append(f"quarantine entry {q.get('id', '?')} is missing {', '.join(missing)}")
        elif not q.get("auto") and dt.date.fromisoformat(str(q["expires"])) < today:
            errors.append(f"quarantine entry {q['id']} expired on {q['expires']}")
    if strict:
        errors += warnings
        warnings = []
    return errors, warnings

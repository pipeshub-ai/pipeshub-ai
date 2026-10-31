"""Propose planned scenarios for one module and write them as stub tests.

The model only returns JSON. The engine writes one stub file per scenario, and
only into a suite with `planned = true`. Model text goes into a docstring only
through doc_safe, and a stub that does not read back as the same planned row
is deleted.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any

from qa import catalog, globs, paths
from qa.config import Config, list_files
from qa.impact import mirror_test
from qa.validate import clean_text

MAX_SCENARIOS, MAX_SOURCES, MAX_TITLE = 20, 200, 120
NEXT = "next: open a PR; a reviewer adds the label qa-scenarios-ok"

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

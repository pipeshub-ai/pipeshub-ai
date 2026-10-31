"""Review a change: its requirements traced to this run's tests, the risks the
rules can see, and an exploratory checklist, rendered as a second PR comment.

PR text and model text are equally untrusted: both reach the comment only
through validate.clean_text, and every traced id must be in this run's plan.
"""

from __future__ import annotations

import re
from typing import Any

from qa import config as config_mod
from qa import globs
from qa.config import Config
from qa.report import BLOCKING, merge_results, redact
from qa.validate import clean_text

MAX_BODY, MAX_ISSUE_BODY, MAX_ISSUES = 20_000, 5_000, 3
MAX_REQUIREMENTS, MAX_CHECKLIST, MAX_MODEL_RISKS = 30, 15, 5
PASSED = {"pass", "flaky"}
COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(\S.*?)\s*$")


def requirements_from_text(text: str, limit: int = 20) -> list[str]:
    """The bullet, numbered and checkbox lines of a PR body, outside HTML comments."""
    out: list[str] = []
    for line in COMMENT.sub("", text or "").splitlines():
        m = BULLET.match(line)
        if m and (item := clean_text(m.group(1))):
            out.append(item)
            if len(out) >= limit:
                break
    return out


def _modules(cfg: Config, files: list[str]) -> list[str]:
    return [m["id"] for m in cfg.modules if any(globs.match_any(f, m.get("paths", [])) for f in files)]


def build_input(plan: dict[str, Any], results: dict[str, Any] | None, changeset: dict[str, Any], pr: dict[str, Any],
                cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or config_mod.load()
    files = [f["path"] for f in changeset["files"]]
    outcome = {t["id"]: t["outcome"] for t in results["tests"]} if results is not None else {}
    return {
        "pr": {
            "number": pr.get("number"),
            "title": pr.get("title") or "",
            "body": (pr.get("body") or "")[:MAX_BODY],
            "issues": [{"number": i.get("number"), "title": i.get("title") or "", "body": (i.get("body") or "")[:MAX_ISSUE_BODY]}
                       for i in (pr.get("issues") or [])[:MAX_ISSUES]],
            "commits": list(pr.get("commits") or []),
        },
        "tests": [{"id": t["id"], "why": t.get("why", []), "flows": t.get("flows", []),
                   "outcome": outcome.get(t["id"], "not_run") if results is not None else None} for t in plan["tests"]],
        "files": files,
        "modules": _modules(cfg, files),
        "flows": sorted(plan.get("flows", {})),
    }


def risks(cfg: Config, plan: dict[str, Any], changeset: dict[str, Any], rows: dict[str, Any]) -> list[dict[str, str]]:
    """Changed P0 flows, then [[review_risk]] hits, then missing tests."""
    out = []
    for fid, why in plan.get("flows", {}).items():
        f = cfg.flow(fid)
        if f and f.get("priority") == "P0":
            out.append({"id": f"flow:{fid}", "why": f"P0 flow {f.get('title', fid)}: {why}"})
    files = [f["path"] for f in changeset["files"]]
    modules = set(_modules(cfg, files))
    for entry in cfg.always.get("review_risk", []):
        hit = next((f for f in files if globs.match_any(f, entry.get("paths", []))), None)
        hit = hit or next((m for m in entry.get("modules", []) if m in modules), None)
        if hit:
            out.append({"id": entry["id"], "why": f"{entry.get('title', entry['id'])}: {hit}"})
    if n := len(plan.get("missing", [])):
        out.append({"id": "missing", "why": f"{n} missing tests"})
    return out


def _result(outcomes: list[str | None]) -> str:
    if not outcomes:
        return "untested"
    if any(o in BLOCKING for o in outcomes):
        return "fail"
    return "pass" if all(o in PASSED for o in outcomes) else "not_run"


def merge(cfg: Config, plan: dict[str, Any], results: dict[str, Any] | None, review_input: dict[str, Any],
          llm: dict[str, Any] | None, rows: dict[str, Any]) -> dict[str, Any]:
    status = dict((llm or {}).get("_planner") or {"adapter": "none", "status": "skipped"})
    usable = llm if llm is not None and status.get("status") == "ok" else None
    plan_ids = [t["id"] for t in plan["tests"]]
    merged = merge_results([results], plan_ids) if results is not None else None
    outcome = {t["id"]: t["outcome"] for t in merged["tests"]} if merged else {}

    dropped: list[dict[str, str]] = []
    requirements: list[dict[str, Any]] = []
    if usable:
        for r in usable.get("requirements", [])[:MAX_REQUIREMENTS]:
            tests: list[dict[str, Any]] = []
            for tid in r.get("tests", []):
                if tid not in plan_ids:
                    dropped.append({"id": clean_text(tid), "why": "not in this plan"})
                elif tid not in {t["id"] for t in tests}:
                    tests.append({"id": tid, "outcome": outcome.get(tid)})
            if text := clean_text(r.get("text")):
                requirements.append({"text": text, "tests": tests, "result": _result([t["outcome"] for t in tests])})
    else:
        requirements = [{"text": t, "tests": [], "result": "untested"}
                        for t in requirements_from_text(review_input["pr"]["body"])]

    scope = plan.get("edition", "all")
    checklist = []
    for c in (usable or {}).get("checklist", [])[:MAX_CHECKLIST]:
        row = rows.get(c.get("test") or "")
        active = row is not None and row.get("status") == "active" and scope in ("all", row.get("edition"))
        checklist.append({"area": clean_text(c.get("area"), 60), "probe": clean_text(c.get("probe")), "test": c["test"] if active else None})

    found = risks(cfg, plan, {"files": [{"path": p} for p in review_input["files"]]}, rows)
    found += [{"id": "model", "why": clean_text(r.get("reason"))} for r in (usable or {}).get("risks", [])[:MAX_MODEL_RISKS]]

    if merged is None:
        not_run = [{"id": t, "kind": "plan only", "reason": "plan only, nothing executed"} for t in plan_ids]
    else:
        not_run = [{"id": t["id"], "kind": t.get("kind", "no_result"), "reason": clean_text(redact(t.get("details") or "unknown"))}
                   for t in merged["tests"] if t["outcome"] == "not_run"]
    return {"requirements": requirements, "dropped": dropped, "risks": found, "checklist": checklist,
            "not_run": not_run, "model": status}


def marker(edition: str) -> str:
    return f"<!-- qa-review:{edition} -->"


def _cell(value: Any) -> str:
    return clean_text(value).replace("|", "/")


def render(rv: dict[str, Any], edition: str, run_url: str | None = None) -> str:
    model = rv.get("model", {})
    lines = [
        marker(edition),
        f"## QA review {edition.upper() if edition != 'all' else ''}".rstrip(),
        "",
        f"Model: {_cell(model.get('adapter', 'none'))}, {_cell(model.get('status', 'skipped'))}"
        + (f" ({_cell(model['error'])})" if model.get("error") else ""),
        "", "### Requirements and tests", "",
    ]
    if rv["requirements"]:
        lines += ["| Requirement | Tests | Result |", "| --- | --- | --- |"]
        for r in rv["requirements"]:
            tests = ", ".join(f"{t['id']} ({t['outcome'] or 'not run'})" for t in r["tests"]) or "none"
            lines.append(f"| {_cell(r['text'])} | {_cell(tests)} | {r['result']} |")
    else:
        lines.append("No requirements found.")
    if rv.get("dropped"):
        lines += ["", "Test ids not in this plan, ignored: " + ", ".join(_cell(d["id"]) for d in rv["dropped"][:30])]

    lines += ["", "### Risks", ""]
    lines += [f"- {_cell(r['why'])}" for r in rv["risks"]] or ["None found."]

    lines += ["", "### Exploratory checklist", ""]
    if rv["checklist"]:
        lines += ["| Area | Probe | Test |", "| --- | --- | --- |"]
        lines += [f"| {_cell(c['area'])} | {_cell(c['probe'])} | {_cell(c['test'] or 'manual')} |" for c in rv["checklist"]]
    else:
        lines.append("No probes proposed.")

    lines += ["", "### Bugs found", ""]
    bugs = rv.get("bugs", [])
    lines += [f"- {_cell(b if isinstance(b, str) else b.get('title') or b.get('id'))}" for b in bugs] or ["None found."]

    lines += ["", "### Not tested", ""]
    lines += [f"- {_cell(n['id'])} ({n['kind']}): {_cell(n['reason'])}" for n in rv["not_run"]]
    lines += [f"- No test checks: {_cell(r['text'])}" for r in rv["requirements"] if r["result"] == "untested"]
    lines += [f"- Manual probe, {_cell(c['area'])}: {_cell(c['probe'])}" for c in rv["checklist"] if not c["test"]]
    if lines[-1] == "":
        lines.append("Everything planned ran.")
    if run_url:
        lines += ["", f"Screenshots, traces and logs: {run_url}"]
    return redact("\n".join(lines) + "\n")

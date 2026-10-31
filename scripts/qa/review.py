"""Review a change: its requirements traced to this run's tests, the risks the
rules can see, and an exploratory checklist, rendered as a second PR comment.

PR text and model text are equally untrusted: both reach the comment only
through validate.clean_text, and every traced id must be in this run's plan.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from qa import config as config_mod
from qa import globs
from qa.config import Config
from qa.report import BLOCKING, merge_results, redact
from qa.validate import MAX_REASON, clean_text

MAX_BODY, MAX_ISSUE_BODY, MAX_ISSUES = 20_000, 5_000, 3
MAX_REQUIREMENTS, MAX_CHECKLIST, MAX_MODEL_RISKS = 30, 15, 5
PASSED = {"pass", "flaky"}
COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(\S.*?)\s*$")
RUN_URL = re.compile(r"https://\S+/actions/runs/\d+")
MAX_PROBES = 10


def gather(pr: int, repo: str | None) -> dict[str, Any]:
    """The PR's title, body, commit headlines and first linked issues, read with gh."""
    def gh(*args: str) -> dict[str, Any]:
        cmd = ["gh", *args] + (["--repo", repo] if repo else [])
        return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)

    data = gh("pr", "view", str(pr), "--json", "title,body,commits,closingIssuesReferences")
    issues = []
    for ref in (data.get("closingIssuesReferences") or [])[:MAX_ISSUES]:
        issue = gh("issue", "view", str(ref["number"]), "--json", "title,body")
        issues.append({"number": ref["number"], "title": issue.get("title") or "", "body": issue.get("body") or ""})
    return {"number": pr, "title": data.get("title") or "", "body": data.get("body") or "", "issues": issues,
            "commits": [c.get("messageHeadline") or "" for c in data.get("commits") or []]}


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
    plan_ids = {t["id"] for t in plan["tests"]}
    merged = merge_results([results], sorted(plan_ids)) if results is not None else None
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
        not_run = [{"id": t["id"], "kind": "plan only", "reason": "plan only, nothing executed"} for t in plan["tests"]]
    else:
        not_run = [{"id": t["id"], "kind": t.get("kind", "no_result"), "reason": clean_text(redact(t.get("details") or "unknown"))}
                   for t in merged["tests"] if t["outcome"] == "not_run"]
    return {"requirements": requirements, "dropped": dropped, "risks": found, "checklist": checklist,
            "not_run": not_run, "model": status}


def probe_ids(rv: dict[str, Any], results: dict[str, Any] | None) -> list[str]:
    """Checklist tests this run did not execute, the ones `qa review --run` probes."""
    ran = {t["id"] for t in (results or {}).get("tests", []) if t["outcome"] != "not_run"}
    ids = dict.fromkeys(c["test"] for c in rv["checklist"] if c["test"] and c["test"] not in ran)
    return list(ids)[:MAX_PROBES]


def bugs(results: dict[str, Any], rows: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": t["id"], "details": redact(t.get("details") or "")[:300],
             "repro": [*rows.get(t["id"], {}).get("steps", []), f"python3 scripts/qa run --ids {t['id']}"]}
            for t in results.get("tests", []) if t["outcome"] in ("fail", "error")]


def marker(edition: str) -> str:
    return f"<!-- qa-review:{edition} -->"


def _cell(value: Any, limit: int = MAX_REASON) -> str:
    return clean_text(value, limit).replace("|", "/")


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
    if rv.get("probes"):
        lines += ["", "Probes not run here. Run them with: " + clean_text("python3 scripts/qa run --ids " + " ".join(rv["probes"]), 2000)]

    lines += ["", "### Bugs found", ""]
    lines += [f"- {_cell(b['id'])}: {_cell(b['details']) or 'no details'}. Repro: {_cell('; '.join(b['repro']), 1000)}"
              for b in rv.get("bugs", [])] or ["None found."]

    lines += ["", "### Not tested", ""]
    lines += [f"- {_cell(n['id'])} ({_cell(n['kind'])}): {_cell(n['reason'])}" for n in rv["not_run"]]
    lines += [f"- No test checks: {_cell(r['text'])}" for r in rv["requirements"] if r["result"] == "untested"]
    lines += [f"- Manual probe, {_cell(c['area'])}: {_cell(c['probe'])}" for c in rv["checklist"] if not c["test"]]
    if lines[-1] == "":
        lines.append("Everything planned ran.")
    # Only a workflow run link from the trusted Actions context, never free text.
    if run_url and RUN_URL.fullmatch(run_url):
        lines += ["", f"Screenshots, traces and logs: {run_url}"]
    return redact("\n".join(lines) + "\n")

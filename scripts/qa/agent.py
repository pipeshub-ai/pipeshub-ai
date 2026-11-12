"""The QA agent: Claude Code plans and judges, the engine runs.

Round 0 runs the rule floor. Then `claude -p` gets the change, the floor and its results, and a
fixed tool set: read and search anything, edit and write files, and Bash limited to the engine
(`run --session`, `catalog`, `prove`, `stack status`) and read-only git. It runs more tests, reads
their results, writes tests, and ends with a verdict file. Every round's results merge into
results-agent.json, which the report and the gate read: the verdict never changes the gate.
Changes outside test files are reverted; the kept test changes go to agent-tests.patch.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from . import catalog, paths, report, session
from .run import MODEL_CREDENTIALS
from .config import Config

SCHEMA = "qa-agent/v1"
NOTEBOOK = "qa-notebook/v1"
# A finding from the previous run, rechecked on the new code.
SINCE = ("fixed", "still-broken", "changed", "not-rechecked")
KINDS = ("regression", "existing-bug", "flaky", "environment", "test-bug", "combined-only")
VERDICTS = ("works", "broken", "unsure")
RAN = {"pass", "fail", "error", "flaky"}
MAX_TEXT = 2000
MAX_ITEMS = 50
# Test edits may also refresh the committed catalog the new tests belong to.
CATALOG_DIRS = ("integration-tests/catalog/",)
DENIED = ("Read(//proc/**)", "Read(//sys/**)")


def engine_cmd(root: Path) -> str:
    """How Claude, working in root, calls this engine: the checkout it runs from (in CI, the base branch's)."""
    main = (paths.REPO / "scripts" / "qa").resolve()
    try:
        main = main.relative_to(root.resolve())
    except ValueError:
        pass
    return f"python3 {main}"


def tools(engine: str) -> list[str]:
    return ["Read", "Glob", "Grep", "Edit", "Write",
            *(f"Bash({engine} {c}:*)" for c in ("run", "catalog", "prove", "stack status")),
            *(f"Bash(git {c}:*)" for c in ("diff", "log", "show", "status"))]


def take_credentials() -> dict[str, str]:
    """Remove Claude's credential from this process's environment, so no test the engine runs inherits it;
    only the claude processes get it back (claude_env)."""
    return {k: v for k in MODEL_CREDENTIALS if (v := os.environ.pop(k, ""))}


def claude_env(creds: dict[str, str]) -> dict[str, str]:
    return {**os.environ, **creds}


def unavailable(creds: dict[str, str]) -> str | None:
    if not shutil.which("claude"):
        return "the claude CLI is not installed"
    if os.environ.get("CI") and not creds:
        return "no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY"
    return None


# --- results across rounds --------------------------------------------------------

def round_dirs(agent_dir: Path) -> list[Path]:
    rounds = agent_dir / "rounds"
    found = [p for p in rounds.iterdir() if p.is_dir() and p.name.isdigit()] if rounds.is_dir() else []
    return sorted(found, key=lambda p: int(p.name))


def merge_rounds(dirs: list[Path]) -> dict[str, dict[str, Any]]:
    """The latest result of each test across rounds. A test that failed and later passed is flaky;
    a later not_run never hides an earlier result."""
    merged: dict[str, dict[str, Any]] = {}
    for d in dirs:
        for part in sorted(d.glob("results*.json")):
            try:
                data = json.loads(part.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for t in data.get("tests", []):
                prev = merged.get(t["id"])
                cur = {**t, "round": int(d.name)}
                if prev and cur["outcome"] == "not_run" and prev["outcome"] != "not_run":
                    continue
                if prev and prev["outcome"] in ("fail", "error") and cur["outcome"] == "pass":
                    cur = {**cur, "outcome": "flaky", "details": f"failed in round {prev['round']}, passed in round {cur['round']}"}
                merged[t["id"]] = cur
    return merged


def results_doc(merged: dict[str, dict[str, Any]], head: str | None, seconds: float) -> dict[str, Any]:
    return {"schema": "qa-results/v1", "scope": "all", "head": head, "started": time.time() - seconds,
            "seconds": round(seconds, 1), "stack_mode": "session", "fatal": [], "deferred": [],
            "tests": [{k: v for k, v in t.items() if k != "round"} for t in merged.values()]}


# --- what Claude may change --------------------------------------------------------

def _changed(root: Path) -> list[tuple[str, str]]:
    out = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"], cwd=root,
                         capture_output=True, text=True, check=True).stdout
    items, parts = [], out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1  # the rename's source path follows
        items.append((code, path))
    return items


def is_test_path(cfg: Config, path: str) -> bool:
    return path.startswith(CATALOG_DIRS) or catalog.suite_of(path, cfg, mocharc=False) is not None


def keep_test_changes(cfg: Config, root: Path, ignore: tuple[str, ...] = (),
                      before: set[tuple[str, str]] | None = None) -> tuple[list[str], list[str]]:
    """Revert every change outside test files that was not already there (before); return (kept, reverted) paths."""
    kept, reverted = [], []
    for code, path in _changed(root):
        if path.startswith(ignore) or (before and (code, path) in before):
            continue
        if is_test_path(cfg, path):
            kept.append(path)
            continue
        if code == "??":
            (root / path).unlink(missing_ok=True)
        else:
            subprocess.run(["git", "checkout", "HEAD", "--", path], cwd=root, capture_output=True, check=False)
        reverted.append(path)
    return kept, reverted


def test_patch(root: Path, kept: list[str]) -> str:
    if not kept:
        return ""
    new = [p for p in kept if subprocess.run(["git", "ls-files", "--error-unmatch", "--", p], cwd=root, capture_output=True).returncode]
    if new:
        subprocess.run(["git", "add", "-N", "--", *new], cwd=root, check=True, capture_output=True)
    try:
        return subprocess.run(["git", "diff", "--binary", "HEAD", "--", *kept], cwd=root, capture_output=True, text=True, check=True).stdout
    finally:
        if new:
            subprocess.run(["git", "reset", "-q", "--", *new], cwd=root, capture_output=True, check=False)


# --- the verdict ----------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value or "")[:MAX_TEXT]


def validate_verdict(raw: Any, known: set[str]) -> tuple[dict[str, Any], list[str]]:
    """Keep only well-formed fields and test ids the engine knows; return (verdict, problems)."""
    problems: list[str] = []
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return {}, [f"the verdict is not a {SCHEMA} object"]
    verdict = raw.get("verdict") if raw.get("verdict") in VERDICTS else "unsure"
    if raw.get("verdict") not in VERDICTS:
        problems.append(f"verdict {str(raw.get('verdict'))[:40]!r} is not one of {', '.join(VERDICTS)}")

    def ids(values: Any) -> list[str]:
        out = []
        for v in (values if isinstance(values, list) else [])[:MAX_ITEMS]:
            if isinstance(v, str) and v in known:
                out.append(v)
            else:
                problems.append(f"unknown test id {str(v)[:120]!r}")
        return out
    findings = []
    for f in (raw.get("findings") if isinstance(raw.get("findings"), list) else [])[:MAX_ITEMS]:
        if not isinstance(f, dict) or f.get("kind") not in KINDS:
            problems.append(f"finding with kind {str(f.get('kind') if isinstance(f, dict) else f)[:40]!r} dropped")
            continue
        findings.append({"kind": f["kind"], "tests": ids(f.get("tests")), "title": _text(f.get("title"))[:200],
                         "evidence": _text(f.get("evidence")),
                         "files": [str(x)[:300] for x in (f.get("files") if isinstance(f.get("files"), list) else [])][:20],
                         "prs": [int(x) for x in (f.get("prs") if isinstance(f.get("prs"), list) else []) if isinstance(x, int)][:10]})
    rounds = []
    for r in (raw.get("rounds") if isinstance(raw.get("rounds"), list) else [])[:MAX_ITEMS]:
        if isinstance(r, dict):
            rounds.append({"why": _text(r.get("why"))[:500], "tests": ids(r.get("tests"))})
    rechecked = []
    for f in (raw.get("since_last") if isinstance(raw.get("since_last"), list) else [])[:MAX_ITEMS]:
        if isinstance(f, dict) and f.get("now") in SINCE:
            rechecked.append({"title": _text(f.get("title"))[:200], "now": f["now"], "evidence": _text(f.get("evidence"))[:500]})
        else:
            problems.append("a since_last entry without a known `now` dropped")
    return {"schema": SCHEMA, "verdict": verdict, "summary": _text(raw.get("summary")), "findings": findings,
            "rounds": rounds, "unproven": ids(raw.get("unproven")), "since_last": rechecked}, problems


# --- the notebook: what the agent knows about one PR, for the next run ---------------------------

def build_notebook(verdict: dict[str, Any], merged: dict[str, dict[str, Any]], plan: dict[str, Any],
                   kept: list[str], pr: int | None, previous: dict[str, Any] | None) -> dict[str, Any]:
    ran = sorted(i for i, t in merged.items() if t["outcome"] in RAN)
    return {
        "schema": NOTEBOOK, "pr": pr, "head": plan.get("head"), "base": plan.get("base"),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": int((previous or {}).get("runs", 0)) + 1,
        "verdict": verdict.get("verdict", "none"), "summary": _text(verdict.get("summary"))[:600],
        "findings": [{"kind": f["kind"], "title": f["title"], "tests": f["tests"][:10], "files": f["files"][:10],
                      "evidence": f["evidence"][:300]} for f in verdict.get("findings", [])][:20],
        "failing": sorted(i for i, t in merged.items() if t["outcome"] in ("fail", "error"))[:50],
        "ran": ran[:200], "tests_added": kept[:20],
    }


def validate_notebook(raw: Any, pr: int | None) -> dict[str, Any] | None:
    """A notebook read back from a PR comment or a file: its own PR's, well formed and small, or nothing."""
    if not isinstance(raw, dict) or raw.get("schema") != NOTEBOOK:
        return None
    if pr is not None and raw.get("pr") != pr:
        return None
    sha = re.compile(r"[0-9a-f]{40}")
    if not (isinstance(raw.get("head"), str) and sha.fullmatch(raw["head"])):
        return None
    out = {k: raw.get(k) for k in ("schema", "pr", "head", "base", "at", "verdict")}
    out["base"] = raw.get("base") if isinstance(raw.get("base"), str) and sha.fullmatch(raw.get("base") or "") else None
    out["at"] = str(raw.get("at") or "")[:40]
    out["verdict"] = raw.get("verdict") if raw.get("verdict") in (*VERDICTS, "none") else "none"
    out["runs"] = raw.get("runs") if isinstance(raw.get("runs"), int) and 0 < raw["runs"] < 10_000 else 1
    out["summary"] = _text(raw.get("summary"))[:600]
    strs = lambda v, n, m: [str(x)[:m] for x in (v if isinstance(v, list) else [])][:n]
    out["findings"] = [{"kind": f.get("kind") if f.get("kind") in KINDS else "test-bug", "title": _text(f.get("title"))[:200],
                        "tests": strs(f.get("tests"), 10, 300), "files": strs(f.get("files"), 10, 300),
                        "evidence": _text(f.get("evidence"))[:300]}
                       for f in (raw.get("findings") if isinstance(raw.get("findings"), list) else [])[:20] if isinstance(f, dict)]
    out["failing"] = strs(raw.get("failing"), 50, 300)
    out["ran"] = strs(raw.get("ran"), 200, 300)
    out["tests_added"] = strs(raw.get("tests_added"), 20, 300)
    return out


def since(root: Path, previous: dict[str, Any] | None, head: str | None) -> dict[str, Any] | None:
    """What changed since the previous run tested this PR: a range, or why there is none."""
    if not previous:
        return None
    head = head or "HEAD"
    last = previous["head"]
    if subprocess.run(["git", "cat-file", "-e", f"{last}^{{commit}}"], cwd=root, capture_output=True).returncode:
        return {"from": last, "note": "the commit last tested is gone (history rewritten): treat this as a first run"}
    if subprocess.run(["git", "merge-base", "--is-ancestor", last, head], cwd=root, capture_output=True).returncode:
        return {"from": last, "note": "the PR was rebased or force-pushed since: compare by behaviour, not by diff"}
    files = subprocess.run(["git", "diff", "--name-only", f"{last}..{head}"], cwd=root, capture_output=True, text=True).stdout.split()
    return {"from": last, "files": files[:200], "unchanged": not files}


def _secrets() -> list[str]:
    names = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CONNECTION_STRING)", re.I)
    return [v for k, v in os.environ.items() if names.search(k) and len(v) >= 8]


def holds_secret(*texts: str, extra: tuple[str, ...] = ()) -> bool:
    values = _secrets() + [v for v in extra if len(v) >= 8]
    return any(v in t for t in texts for v in values)


# --- the loop ------------------------------------------------------------------

def _input(out: Path, plan: dict[str, Any], merged: dict[str, dict[str, Any]], sess: dict[str, Any] | None,
           engine: str, minutes: float, combined: dict[str, Any] | None, changed: list[str],
           previous: dict[str, Any] | None = None, since_last: dict[str, Any] | None = None) -> Path:
    agent_dir = out / "agent"
    data = {
        "change": {"base": plan.get("base"), "head": plan.get("head"), "mode": plan.get("mode"),
                   "diff": str(agent_dir / "changeset.diff"), "files": changed},
        "floor": [{"id": t["id"], "why": t.get("why", []), "module": t.get("module")} for t in plan["tests"]],
        "round0": {"pass": sorted(i for i, t in merged.items() if t["outcome"] == "pass"),
                   "other": [{"id": i, "outcome": t["outcome"], "kind": t.get("kind"), "details": _text(t.get("details"))}
                             for i, t in sorted(merged.items()) if t["outcome"] != "pass"]},
        "catalog": str(out / "catalog-rows.json"),
        "stack": ({"base_url": sess["base_url"], "graph_db": sess["graph_db"], "profiles": sess["compose_profiles"],
                   "session": str(session.path(out))} if sess else None),
        "commands": {
            "run": f"{engine} run --out {agent_dir}/rounds/<N> --ids <id> [<id> ...]" + (f" --session {session.path(out)}" if sess else ""),
            "catalog": f"{engine} catalog build",
            "prove": f"{engine} prove <id> --out {agent_dir}/prove/<N> --base {plan.get('base')}",
        },
        "minutes": minutes,
        "verdict_file": str(agent_dir / "verdict.json"),
        "combined": combined,
        # What the agent found on this PR before, and what changed since: recheck those findings first.
        "previous": previous,
        "since_last": since_last,
    }
    path = agent_dir / "input.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _claude(root: Path, prompt: str, engine: str, minutes: float, max_turns: int, log: Path, creds: dict[str, str]) -> str:
    cmd = [shutil.which("claude") or "claude", "-p", prompt, "--output-format", "json", "--max-turns", str(max_turns),
           "--allowedTools", ",".join(tools(engine)), "--disallowedTools", ",".join(DENIED),
           # User settings only and no MCP servers: the tree under test could add hooks, tools or servers.
           "--setting-sources", "user", "--strict-mcp-config"]
    if os.environ.get("QA_PLANNER_MODEL") and os.environ.get("QA_PLANNER", "claude_code") == "claude_code":
        cmd += ["--model", os.environ["QA_PLANNER_MODEL"]]
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=root, env=claude_env(creds), stdout=subprocess.PIPE, stderr=fh, text=True, start_new_session=True)
        try:
            stdout, _ = proc.communicate(timeout=minutes * 60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
            return f"stopped at its {minutes:.0f}-minute limit"
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        envelope = None
    if proc.returncode or not isinstance(envelope, dict) or envelope.get("is_error"):
        detail = " ".join(str(envelope.get(k)) for k in ("subtype", "result") if isinstance(envelope, dict) and envelope.get(k))
        return f"claude exited {proc.returncode}: {(detail or stdout.strip())[:300]}"
    return ""


def run_agent(cfg: Config, out: Path, plan: dict[str, Any], run_round: Callable[[list[str], Path], None], *,
              sess: dict[str, Any] | None, minutes: float, max_turns: int,
              combined: dict[str, Any] | None = None, creds: dict[str, str] | None = None,
              previous: dict[str, Any] | None = None, pr: int | None = None) -> dict[str, Any]:
    """run_round(ids, round_dir) runs tests through the engine (round 0 is the floor). Returns agent.json's content."""
    root = cfg.root
    # Before round 0: no test the engine runs may inherit Claude's credential.
    creds = creds if creds is not None else take_credentials()
    out = out.resolve()  # Claude works in root; every path it is given must not depend on this process's cwd
    agent_dir = out / "agent"
    (agent_dir / "rounds").mkdir(parents=True, exist_ok=True)
    started = time.time()
    run_round([t["id"] for t in plan["tests"]], agent_dir / "rounds" / "0")
    notes: list[str] = []
    reason = unavailable(creds)
    # Only what Claude changes is judged: files round 0 or the checkout already had stay as they are.
    before = set(_changed(root))
    if reason:
        notes.append(f"agent unavailable: {reason}; the floor alone ran")
    else:
        diff = subprocess.run(["git", "diff", f"{plan['base']}..{plan.get('head') or 'HEAD'}"], cwd=root, capture_output=True, text=True)
        (agent_dir / "changeset.diff").write_text(diff.stdout, encoding="utf-8")
        engine = engine_cmd(root)
        changed = subprocess.run(["git", "diff", "--name-only", f"{plan['base']}..{plan.get('head') or 'HEAD'}"], cwd=root,
                                 capture_output=True, text=True).stdout.split()
        inp = _input(out, plan, merge_rounds(round_dirs(agent_dir)), sess, engine, minutes, combined, changed,
                     previous, since(root, previous, plan.get("head")))
        prompt = (paths.REPO / "scripts/qa/prompts/agent.md").read_text(encoding="utf-8")
        prompt += f"\n\n## This run\n\nRead `{inp}` first. Write the verdict to `{agent_dir / 'verdict.json'}`.\n"
        problem = _claude(root, prompt, engine, minutes, max_turns, agent_dir / "claude.log", creds)
        if problem:
            notes.append(f"agent {problem}")
    ignore = tuple(p for p in (os.path.relpath(out, root) + "/",) if not p.startswith(".."))
    kept, reverted = keep_test_changes(cfg, root, ignore, before)
    if reverted:
        notes.append("reverted changes outside test files: " + ", ".join(reverted[:20]))
    merged = merge_rounds(round_dirs(agent_dir))
    known = set(merged) | {t["id"] for t in plan["tests"]}
    raw: Any = None
    vfile = agent_dir / "verdict.json"
    if vfile.is_file():
        try:
            raw = json.loads(vfile.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            notes.append(f"the verdict is not JSON: {exc}")
    elif not reason:
        notes.append("the agent wrote no verdict")
    verdict, problems = validate_verdict(raw, known) if raw is not None else ({}, [])
    notes += problems[:20]
    patch = test_patch(root, kept)
    if holds_secret(json.dumps(verdict), patch, json.dumps(notes), extra=tuple(creds.values())):
        verdict, patch, kept = {}, "", []
        notes = ["the agent's output held a secret value; its verdict and test changes were dropped"]
    (out / "agent-tests.patch").write_text(patch, encoding="utf-8")
    plan_ids = {t["id"] for t in plan["tests"]}
    extra = [{"id": i, "source": "agent", "why": ["run by the QA agent"], "module": None, "flows": [], "grep": None}
             for i in merged if i not in plan_ids]
    results = results_doc(merged, plan.get("head"), time.time() - started)
    report.redact_results(results)
    (out / "results-agent.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    doc = {"schema": SCHEMA, "verdict": verdict, "notes": notes, "tests_added": kept, "extra_tests": extra,
           "rounds": len(round_dirs(agent_dir)), "combined": combined,
           "previous": {k: previous.get(k) for k in ("head", "at", "verdict", "runs")} if previous else None}
    if verdict and not combined:
        notebook = build_notebook(verdict, merged, plan, kept, pr, previous)
        if not holds_secret(json.dumps(notebook), extra=tuple(creds.values())):
            doc["notebook"] = notebook
            (out / "agent-notebook.json").write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    (out / "agent.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


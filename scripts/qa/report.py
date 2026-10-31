"""Render the plan and results as one PR comment, a step summary, and a gate."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from qa import junit
from qa.config import Config

BLOCKING = {"fail", "error"}
NOT_RUN_KINDS = ("secret", "scope", "profile", "infra", "toolchain", "cancelled", "no_result")
# Only a test that lacked a secret, sat outside the scope or was deferred by the profile may go unrun without blocking.
DEFAULT_ALLOWED = frozenset({"secret", "scope", "profile"})
ORDER = ["fail", "error", "flaky", "not_run", "skip", "pass"]


SECRET_NAME = re.compile(r"(?i)(TOKEN|KEY|SECRET|PASSWORD|CONNECTION_STRING|SERVICE_ACCOUNT_JSON)")


def redact(text: str, env: Mapping[str, str] = os.environ) -> str:
    """Hide the value of every secret-named variable (6+ characters) in text; longest first."""
    values = {v for k, v in env.items() if SECRET_NAME.search(k) and len(v) >= 6}
    # shortcut: a value only matches as written; its JSON-escaped or URL-encoded form does not. Also hide those forms once a secret with such characters is seen.
    for v in sorted(values, key=len, reverse=True):
        text = text.replace(v, "***")
    return text


def redact_results(result: dict[str, Any]) -> None:
    """Hide secrets in test details and fatal messages, while the run's env still has them."""
    for t in result.get("tests", []):
        if t.get("details"):
            t["details"] = redact(t["details"])
    result["fatal"] = [redact(f) for f in result.get("fatal", [])]


def redact_files(out: Path) -> None:
    """Hide secrets in the logs and JUnit files the run job uploads."""
    for p in [*out.glob("junit-*.xml"), *(out / "logs").rglob("*")]:
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="surrogateescape")
            clean = redact(text)
            if clean != text:
                p.write_text(clean, encoding="utf-8", errors="surrogateescape")


def marker(edition: str) -> str:
    return f"<!-- qa-engine:{edition} -->"


def verdict(cfg: Config, results: dict[str, Any] | None, allowed: frozenset[str] = DEFAULT_ALLOWED) -> tuple[str, list[str]]:
    """("pass"|"fail"|"plan-only", blocking ids)."""
    if results is None:
        return "plan-only", []
    quarantined = cfg.quarantined_ids()
    blocking = [
        t["id"] for t in results["tests"]
        if (t["outcome"] in BLOCKING or (t["outcome"] == "not_run" and t.get("kind", "no_result") not in allowed))
        and t["id"] not in quarantined
    ]
    if results.get("fatal"):
        blocking.append("infra")
    return ("fail" if blocking else "pass"), blocking


def _one_line(text: str | None, limit: int = 240) -> str:
    """Runner output in a list item: first line of each failure, no markdown breakage."""
    parts = [p.strip().splitlines()[0] if p.strip() else "" for p in (text or "").split(" | ")]
    out = " | ".join(p for p in parts if p).replace("|", "\\|").replace("`", "'")
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _short(test_id: str) -> str:
    return test_id if len(test_id) <= 110 else "…" + test_id[-109:]


def render(cfg: Config, plan: dict[str, Any], results: dict[str, Any] | None, allowed: frozenset[str] = DEFAULT_ALLOWED, run_url: str | None = None) -> str:
    v, _ = verdict(cfg, results, allowed)
    title = {"pass": "passed", "fail": "failed", "plan-only": "plan only (nothing executed)"}[v]
    planner = plan.get("planner", {})
    from_model = [t for t in plan["tests"] if t["source"] == "model"]
    lines = [
        marker(plan["edition"]),
        f"## QA {plan['edition'].upper() if plan['edition'] != 'all' else ''} — {title}".replace("  ", " "),
        "",
        (
            f"Profile **{plan['profile']}** · mode {plan['mode']} · `{plan['base'][:10]}..{(plan['head'] or '')[:10]}` · "
            f"{len(plan['tests'])} tests selected ({len(plan['tests']) - len(from_model)} by rules, {len(from_model)} by the model)"
        ),
        "",
        f"Planner: {planner.get('adapter', 'none')} — {planner.get('status', 'skipped')}"
        + (f" ({planner['error']})" if planner.get("error") else "")
        + (" — the floor alone ran" if planner.get("status") == "failed" else ""),
    ]
    if run_url:
        lines += ["", f"[Run logs]({run_url})"]
    for note in plan.get("notes", []):
        lines += ["", f"Note: {note}"]

    if results is not None:
        counts = Counter(t["outcome"] for t in results["tests"])
        lines += ["", "| Outcome | Tests |", "| --- | --- |"]
        lines += [f"| {o} | {counts[o]} |" for o in ORDER if counts.get(o)]
        if results.get("fatal"):
            lines += ["", "**Infrastructure problem:** " + "; ".join(results["fatal"])]
        env = results.get("environment")
        if env:
            digests = sorted({d for d in env.get("image_digests") or [env.get("image_digest")] if d})
            lines += ["", "### Environment", "", f"- Image `{env.get('image', '?')}`" + (": " + ", ".join(f"`{d}`" for d in digests) if digests else "")]
            lines += [f"- Base `{name}`: `{ref}`" for name, ref in sorted((env.get("base_images") or {}).items())]
            if env.get("unpinned"):
                lines.append("- Not pinned: " + ", ".join(f"`{r}`" for r in env["unpinned"]))
        failing = [t for t in results["tests"] if t["outcome"] in BLOCKING]
        if failing:
            lines += ["", "### Failures", ""]
            quarantined = cfg.quarantined_ids()
            for t in failing[:40]:
                q = " (quarantined, not blocking)" if t["id"] in quarantined else ""
                lines.append(f"- `{_short(t['id'])}` — {t['outcome']}{q}: {_one_line(redact(t.get('details') or ''))}")
        flaky = [t for t in results["tests"] if t["outcome"] == "flaky"]
        if flaky:
            lines += ["", "### Flaky (passed on retry, not blocking)", ""]
            lines += [f"- `{_short(t['id'])}`" for t in flaky[:20]]
        quarantined = cfg.quarantined_ids()
        held = [t for t in results["tests"] if t["id"] in quarantined]
        if held:
            issue = {q["id"]: q.get("issue") for q in cfg.quarantine}
            lines += ["", "### Quarantined (ran, not blocking)", ""]
            lines += [f"- `{_short(t['id'])}`: {t['outcome']} ({issue.get(t['id'])})" for t in held]
        not_run = defaultdict(list)
        for t in results["tests"]:
            if t["outcome"] == "not_run":
                not_run[(t.get("kind", "no_result"), t.get("details") or "unknown")].append(t["id"])
        if not_run:
            lines += ["", "### Not run", "", "| Kind | Reason | Tests |", "| --- | --- | --- |"]
            for (kind, reason), ids in sorted(not_run.items(), key=lambda kv: -len(kv[1])):
                lines.append(f"| {kind}{'' if kind in allowed else ' (blocks)'} | {_one_line(redact(reason))} | {len(ids)} |")

    # Plans written before flows existed have no `flows` keys: every test lands in (no flow).
    order = {f["id"]: i for i, f in enumerate(cfg.flows)}
    by_flow: dict[str, list[str]] = defaultdict(list)
    for t in plan["tests"]:
        for fid in t.get("flows") or ["(no flow)"]:
            by_flow[fid].append(t["id"])
    outcome = {t["id"]: t["outcome"] for t in results["tests"]} if results is not None else {}

    def flow_key(fid: str) -> tuple[str, int]:
        f = cfg.flow(fid)
        return (f["priority"] if f else "?", order.get(fid, len(order)))
    rows = sorted((f for f in by_flow if f != "(no flow)"), key=flow_key) + (["(no flow)"] if "(no flow)" in by_flow else [])
    if rows:
        lines += ["", "### By flow", "", "| Flow | Priority | Tests | Failed | Not run |", "| --- | --- | --- | --- | --- |"]
        for fid in rows:
            ids = by_flow[fid]
            f = cfg.flow(fid)
            prio = "" if fid == "(no flow)" else f["priority"] if f else "?"
            failed = "" if results is None else sum(outcome.get(i) in BLOCKING for i in ids)
            not_run = "" if results is None else sum(outcome.get(i) == "not_run" for i in ids)
            lines.append(f"| {fid} | {prio} | {len(ids)} | {failed} | {not_run} |")
    untested = [fid for fid in plan.get("flows", {}) if fid not in by_flow]
    if untested:
        lines += ["", "Affected flows with no planned test: " + ", ".join(untested)]

    reasons: dict[str, list[str]] = defaultdict(list)
    for t in plan["tests"]:
        key = t["why"][0] if t["why"] else "unspecified"
        key = key.split(" (")[0] if key.startswith("imports ") else key
        reasons[key].append(t["id"])
    lines += ["", "<details><summary>Why these tests</summary>", "", "| Reason | Tests | Example |", "| --- | --- | --- |"]
    for reason, ids in sorted(reasons.items(), key=lambda kv: -len(kv[1]))[:25]:
        lines.append(f"| {reason} | {len(ids)} | `{_short(ids[0])}` |")
    lines += ["", "</details>"]

    if from_model:
        lines += ["", "### Added by the model", ""]
        lines += [f"- `{_short(t['id'])}` — {t['why'][0] if t['why'] else ''}" for t in from_model[:30]]
    if plan.get("dropped") or plan.get("budget_trimmed"):
        lines += ["", "<details><summary>Model suggestions not used</summary>", ""]
        lines += [f"- `{d['id']}` — {d['why']}" for d in plan.get("dropped", [])[:30]]
        lines += [f"- `{d['id']}` — {d['why']}" for d in plan.get("budget_trimmed", [])[:30]]
        lines += ["", "</details>"]
    if plan.get("missing"):
        lines += ["", "### Missing tests", "", "| Source | Level | What to test | Suggested |", "| --- | --- | --- | --- |"]
        for m in plan["missing"][:40]:
            what = m.get("spec") or m.get("why", "")
            sugg = m.get("suggested_tc") or m.get("suggested_path") or ""
            lines.append(f"| `{m['source']}` | {m['level']} | {what} | {sugg} |")
    if plan.get("unmapped"):
        lines += ["", "<details><summary>Changed files no rule maps to a test</summary>", ""]
        lines += [f"- `{u}`" for u in plan["unmapped"][:50]]
        lines += ["", "</details>"]
    if plan.get("deferred"):
        lines += ["", f"Deferred by the {plan['profile']} profile: {len(plan['deferred'])} integration tests (run with `/qa full` or a standard run)."]
    if plan.get("matrix"):
        lines += ["", "Backend matrix to run: " + ", ".join(plan["matrix"])]
    if plan.get("risk", {}).get("level") in ("medium", "high"):
        lines += ["", f"Risk: {plan['risk']['level']} — " + "; ".join(plan["risk"].get("reasons", []))]
    return redact("\n".join(lines) + "\n")


def merge_results(parts: list[dict[str, Any]], planned: list[str] = ()) -> dict[str, Any]:
    """Combine results from separate run jobs; an executed outcome beats not_run,
    and between executed outcomes the worst wins (a pass on one graph DB must not hide a fail on the other).

    A planned test no job reported is not_run, so the report never counts it as covered.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for part in parts:
        for t in part["tests"]:
            prev = by_id.get(t["id"])
            if prev is None or (prev["outcome"] == "not_run" and (t["outcome"] != "not_run" or _blocks_more(t, prev))):
                by_id[t["id"]] = t
                continue
            both_ran = "not_run" not in (prev["outcome"], t["outcome"])
            worse = junit.worst([prev["outcome"], t["outcome"]]) != prev["outcome"]
            if both_ran and worse:
                by_id[t["id"]] = t
    for test_id in planned:
        by_id.setdefault(test_id, {"id": test_id, "outcome": "not_run", "kind": "no_result", "details": "no result: no run covered it", "seconds": 0.0})
    envs = [p["environment"] for p in parts if p.get("environment")]
    return {
        # Every stack job builds its own image from the same commit; the digests show whether the builds matched.
        **({"environment": {**envs[0], "image_digests": [e.get("image_digest") for e in envs]}} if envs else {}),
        "schema": "qa-results/v1",
        "tests": sorted(by_id.values(), key=lambda t: t["id"]),
        "fatal": [f for p in parts for f in p.get("fatal", [])],
        "invocations": [i for p in parts for i in p.get("invocations", [])],
        "seconds": sum(p.get("seconds", 0) for p in parts),
    }


def _blocks_more(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Each run job lists the other job's tests as not_run scope; any other part's reason must not be hidden by it."""
    def rank(t: dict[str, Any]) -> int:
        kind = t.get("kind", "no_result")
        return 0 if kind == "scope" else 1 if kind in DEFAULT_ALLOWED else 2
    return rank(a) > rank(b)


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def post_comment(repo: str, pr: int, body: str, edition: str) -> str:
    """Create or update the sticky comment for this edition; returns its URL."""
    def gh(*args: str, data: dict[str, Any] | None = None) -> str:
        cmd = ["gh", "api", *args] + (["--input", "-"] if data is not None else [])
        return subprocess.run(
            cmd, input=json.dumps(data) if data is not None else None,
            capture_output=True, text=True, check=True,
        ).stdout

    body = redact(body)
    tag = marker(edition)
    found = gh(
        f"repos/{repo}/issues/{pr}/comments", "--paginate",
        "--jq", f'.[] | select(.body | startswith("{tag}")) | .id',
    ).split()
    if found:
        res = gh("-X", "PATCH", f"repos/{repo}/issues/comments/{found[0]}", data={"body": body})
    else:
        res = gh("-X", "POST", f"repos/{repo}/issues/{pr}/comments", data={"body": body})
    return json.loads(res).get("html_url", "") if res.strip() else ""

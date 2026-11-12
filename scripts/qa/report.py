"""Render the plan and results as one PR comment, a step summary, and a gate."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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


def _model_text(text: str, limit: int) -> str:
    """Model-written text in a PR comment: no mentions, no HTML comments (the sticky markers), one line."""
    text = _one_line(" ".join(redact(str(text or "")).split()), limit)
    return text.replace("@", "@\u200b").replace("<!--", "&lt;!--")


def agent_section(doc: dict[str, Any]) -> list[str]:
    """The QA agent's analysis. It never changes the gate, which reads the test results only."""
    v = doc.get("verdict") or {}
    lines = ["", "### QA agent", ""]
    combined = doc.get("combined") or {}
    if combined.get("prs"):
        lines.append("Combined run of " + ", ".join(f"#{p['number']}" for p in combined["prs"]) + f" on `{str(combined.get('base', ''))[:10]}`.")
        for c in combined.get("conflicts", []):
            lines.append(f"- Conflict: #{c['pr']} against " + ", ".join(f"#{n}" for n in c.get("against", [])) +
                         " in " + ", ".join(f"`{f}`" for f in c.get("files", [])[:10]) + f" (resolved in `{str(c.get('commit', ''))[:10]}`)")
        lines.append("")
    if v:
        lines += [f"**Verdict: {v.get('verdict', 'unsure')}** (Claude's judgment; the gate comes from the test results only)", "",
                  _model_text(v.get("summary"), 1500)]
        if v.get("findings"):
            lines += ["", "| Kind | Finding | Tests | Evidence |", "| --- | --- | --- | --- |"]
            for f in v["findings"][:20]:
                tests = ", ".join(f"`{_short(t)}`" for t in f.get("tests", [])[:3]) + (f" +{len(f['tests']) - 3}" if len(f.get("tests", [])) > 3 else "")
                prs = (" (" + ", ".join(f"#{n}" for n in f.get("prs", [])) + ")") if f.get("prs") else ""
                lines.append(f"| {f['kind']} | {_model_text(f.get('title'), 120)}{prs} | {tests} | {_model_text(f.get('evidence'), 300)} |")
        if v.get("rounds"):
            lines += ["", f"Rounds: {len(v['rounds'])} (round 0 is the rule floor the engine ran first)"]
            lines += [f"- {_model_text(r.get('why'), 200)} ({len(r.get('tests', []))} tests)" for r in v["rounds"][:10]]
    added = doc.get("tests_added") or []
    if added:
        lines += ["", "Test files the agent added or changed (pushed as one commit when the run allows it):"]
        lines += [f"- `{p}`" for p in added[:20]]
    if v.get("unproven"):
        lines += ["", "Added but not proven: " + ", ".join(f"`{_short(t)}`" for t in v["unproven"][:10])]
    for note in doc.get("notes", [])[:10]:
        lines += ["", f"Note: {_model_text(note, 400)}"]
    return lines


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

    if plan.get("agent"):
        lines += agent_section(plan["agent"])
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
            # Filled in by render's caller loop: the one part whose length is trimmed to fit a comment.
            lines.append(FAILURES)
        flaky = [t for t in results["tests"] if t["outcome"] == "flaky"]
        if flaky:
            lines += ["", "### Flaky (passed on retry, not blocking)", ""]
            lines += [f"- `{_short(t['id'])}`" for t in flaky[:20]]
            lines += [f"- … and {len(flaky) - 20} more (see results.json)"] if len(flaky) > 20 else []
        quarantined = cfg.quarantined_ids()
        held = [t for t in results["tests"] if t["id"] in quarantined]
        if held:
            issue = {q["id"]: q.get("issue") for q in cfg.quarantine}
            lines += ["", "### Quarantined (ran, not blocking)", ""]
            lines += [f"- `{_short(t['id'])}`: {t['outcome']}" + (f" ({issue[t['id']]})" if issue.get(t["id"]) else "")
                      for t in held[:20]]
            lines += [f"- … and {len(held) - 20} more (see results.json)"] if len(held) > 20 else []
        not_run = defaultdict(list)
        for t in results["tests"]:
            if t["outcome"] == "not_run":
                not_run[(t.get("kind", "no_result"), t.get("details") or "unknown")].append(t["id"])
        if not_run:
            lines += ["", "### Not run", "", "| Kind | Reason | Tests |", "| --- | --- | --- |"]
            ranked = sorted(not_run.items(), key=lambda kv: -len(kv[1]))
            for (kind, reason), ids in ranked[:25]:
                lines.append(f"| {kind}{'' if kind in allowed else ' (blocks)'} | {_one_line(redact(reason))} | {len(ids)} |")
            if len(ranked) > 25:
                lines.append(f"| … | and {len(ranked) - 25} more reasons (see results.json) | {sum(len(ids) for _, ids in ranked[25:])} |")

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
    # Plans written before modules were recorded have none: the table is left out.
    by_module: dict[str, list[str]] = defaultdict(list)
    for t in plan["tests"]:
        if "module" in t:
            by_module[t["module"] or "(no module)"].append(t["id"])
    if by_module:
        lines += ["", "<details><summary>By module</summary>", "", "| Module | Tests | Failed | Not run |", "| --- | --- | --- | --- |"]
        for mid in sorted(by_module, key=lambda m: (m == "(no module)", m)):
            ids = by_module[mid]
            failed = "" if results is None else sum(outcome.get(i) in BLOCKING for i in ids)
            not_run = "" if results is None else sum(outcome.get(i) == "not_run" for i in ids)
            lines.append(f"| {mid} | {len(ids)} | {failed} | {not_run} |")
        lines += ["", "</details>"]
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
    if FAILURES not in lines:
        return redact("\n".join(lines) + "\n")
    failing = [t for t in results["tests"] if t["outcome"] in BLOCKING]
    quarantined = cfg.quarantined_ids()
    for cap in FAILURE_CAPS:
        body = redact("\n".join(lines).replace(FAILURES, _failures(failing, quarantined, cap)) + "\n")
        if len(body) <= MAX_BODY:
            return body
    return body


FAILURES = "\x00failures\x00"
# GitHub refuses a comment over 65,536 characters; the run's comment adds a header and the improve section.
MAX_BODY = 45_000
FAILURE_CAPS = (25, 10, 5, 2, 0)


def _failures(failing: list[dict[str, Any]], quarantined: set[str], cap: int) -> str:
    """Every failure, grouped by suite family (the id's prefix), largest group first; each group lists at
    most cap and says how many more there are."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in failing:
        groups[t["id"].split(":", 1)[0]].append(t)
    out = ["", "### Failures", "", ", ".join(f"{g}: {len(ts)}" for g, ts in sorted(groups.items(), key=lambda kv: -len(kv[1])))]
    for g, ts in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out += ["", f"#### {g} ({len(ts)})", ""]
        for t in ts[:cap]:
            q = " (quarantined, not blocking)" if t["id"] in quarantined else ""
            out.append(f"- `{_short(t['id'])}` — {t['outcome']}{q}: {_one_line(redact(t.get('details') or ''))}")
        if len(ts) > cap:
            out.append(f"- … and {len(ts) - cap} more in {g} (see results.json)")
    return "\n".join(out)


# A run job lists the tests another job owns as not_run scope with one of these details.
DEFERRED = re.compile(r"outside --scope (unit|stack)|runs in another stack shard|runs on the .+ stack variant")


def job_results(text: str | None) -> dict[str, str]:
    """QA_JOB_RESULTS, e.g. "unit=success,stack=cancelled": how CI's run jobs ended."""
    return dict(kv.split("=", 1) for kv in (text or "").split(",") if "=" in kv)


def merge_results(parts: list[dict[str, Any]], planned: list[str] = (), jobs: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Combine results from separate run jobs; an executed outcome beats not_run,
    and between executed outcomes the worst wins (a pass on one graph DB must not hide a fail on the other).

    A planned test no job reported is not_run, so the report never counts it as covered. jobs (CI only):
    how each run job ended; a test left with another job's scope deferral then names that job instead,
    as cancelled when it was, else no_result. Both block the gate.
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
    for test_id, t in by_id.items():
        m = DEFERRED.match(str(t.get("details") or "")) if jobs and t["outcome"] == "not_run" and t.get("kind") == "scope" else None
        if m:
            owner = "unit" if m.group(1) == "stack" else "stack"
            ended = jobs.get(owner, "")
            by_id[test_id] = {**t, "kind": "cancelled" if ended == "cancelled" else "no_result",
                              "details": f"the {owner} test job {'was cancelled' if ended == 'cancelled' else 'did not report it'}"
                                         + (f" ({ended})" if ended not in ("", "cancelled") else "")}
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


MAX_COMMENT = 65_000
TRUNCATED = "\n\n**Report truncated, see results.json in the run's artifacts.**\n"
RUN_MARKER = "<!-- qa-run:{} -->"
MINIMIZE = "mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) { minimizedComment { isMinimized } } }"


def run_header(env: Mapping[str, str] = os.environ) -> str:
    """One line for a /qa run's comment: the command, who asked (no mention), the head tested and the run."""
    command = re.sub(r"[^A-Za-z0-9 ._/:-]", "", env.get("QA_COMMENT_COMMAND", ""))[:80] or "/qa"
    actor = env.get("QA_COMMENT_ACTOR", "")
    head = env.get("QA_COMMENT_HEAD", "")
    url = env.get("QA_COMMENT_URL", "")
    parts = [f"`{command}`"]
    if re.fullmatch(r"[A-Za-z0-9-]{1,39}", actor):
        parts.append(f"requested by {actor}")
    if re.fullmatch(r"[0-9a-f]{40}", head):
        parts.append(f"at `{head[:12]}`")
    line = " ".join(parts) + "."
    if re.fullmatch(r"https://[A-Za-z0-9._/-]+", url):
        line += f" [Run]({url})"
    return line


def post_comment(repo: str, pr: int, body: str, edition: str, tag: str | None = None) -> str:
    """Post this run's comment and return its URL.

    With QA_COMMENT_RUN (a dispatched /qa run's id) every run posts a new comment, marked with the run, and
    the bot's older comments under the same tag from other /qa runs are collapsed as outdated. Without it
    (the automatic per-push run) one sticky comment is edited. Only the bot's own comments are matched:
    anyone can post one that starts with the tag."""
    def gh(*args: str, data: dict[str, Any] | None = None) -> str:
        cmd = ["gh", "api", *args] + (["--input", "-"] if data is not None else [])
        return subprocess.run(
            cmd, input=json.dumps(data) if data is not None else None,
            capture_output=True, text=True, check=True,
        ).stdout

    body = redact(body)
    tag = tag or marker(edition)
    author = os.environ.get("QA_COMMENT_AUTHOR", "github-actions[bot]")
    mine = f'.user.login == "{author}" and (.body | startswith("{tag}"))'
    run_id = os.environ.get("QA_COMMENT_RUN", "")
    if run_id.isdigit():
        run = RUN_MARKER.format(run_id)
        first, _, rest = body.partition("\n") if body.startswith(tag) else (tag, "", body)
        res = gh("-X", "POST", f"repos/{repo}/issues/{pr}/comments",
                 data={"body": _fit(f"{first}\n{run}\n{run_header()}\n\n{rest}")})
        try:
            older = gh(f"repos/{repo}/issues/{pr}/comments", "--paginate", "--jq",
                       f'.[] | select({mine} and (.body | contains("<!-- qa-run:")) and (.body | contains("{run}") | not)) | .node_id').split()
            for node in older:
                gh("graphql", "-f", f"query={MINIMIZE}", "-f", f"id={node}")
        except (OSError, subprocess.CalledProcessError) as exc:
            # Collapsing old runs is a courtesy: it never fails the report.
            print(f"::warning::could not collapse older QA comments: {getattr(exc, 'stderr', '') or exc}"[:300], file=sys.stderr)
    else:
        # The per-push sticky comment, never one of a /qa run's comments.
        found = gh(f"repos/{repo}/issues/{pr}/comments", "--paginate", "--jq",
                   f'.[] | select({mine} and (.body | contains("<!-- qa-run:") | not)) | .id').split()
        if found:
            res = gh("-X", "PATCH", f"repos/{repo}/issues/comments/{found[0]}", data={"body": _fit(body)})
        else:
            res = gh("-X", "POST", f"repos/{repo}/issues/{pr}/comments", data={"body": _fit(body)})
    return json.loads(res).get("html_url", "") if res.strip() else ""


def _fit(body: str) -> str:
    """The last guard before GitHub's limit; the report's own caps should already have kept it under."""
    return body if len(body) <= MAX_COMMENT else body[:MAX_COMMENT - len(TRUNCATED)] + TRUNCATED

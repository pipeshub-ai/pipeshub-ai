"""Shadow recall: would the floor have selected the tests the existing workflows saw fail?

Each comparison takes one failed run of an existing workflow, maps its JUnit failures to
catalog ids, and checks them against the floor plan (rules only, no model) computed in a
worktree at the commit the run tested: its head, or for a pull_request run the merge of
its head into the base branch. A failure the catalog cannot place counts as a miss.
Runs from forks are skipped, and a failure counts once per pull request (or branch).

The switch to enforcement needs 14 days of data, 20 failures and recall >= 0.98.
A human flips QA_ENFORCE; nothing here does.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from qa import branchstore, catalog, collect, config, impact, junit
from qa.config import Config

MAX_LISTED = 200  # missed and unmapped ids kept per record; the counts keep the rest

# Artifact name pattern and catalog id prefix of each workflow's JUnit files. The py shards
# run from backend/python, the integration shards from integration-tests.
WORKFLOW_ARTIFACTS = {"unit-test.yml": [("py-junit-*", "py")], "integration-tests.yml": [("reports-*", "it")]}
FAMILIES = sorted({prefix for pairs in WORKFLOW_ARTIFACTS.values() for _, prefix in pairs})
POPULATION = (f"Covers failures of the {' and '.join(FAMILIES)} test families seen by "
              f"{', '.join(sorted(WORKFLOW_ARTIFACTS))} only; not whole-suite recall.")


def failures(cases: list[junit.Case], rows: list[dict[str, Any]], prefix: str) -> tuple[set[str], list[str]]:
    """Catalog ids of the failed cases, and "<classname>::<name>" for the ones no row explains."""
    ids = {r["id"] for r in rows}
    files = sorted({i.split("::")[0].split(":", 1)[1] for i in ids if i.startswith(prefix + ":")})
    modules = junit.module_names(files)
    mapped, unmapped = set(), set()
    for c in cases:
        if c.outcome not in ("fail", "error"):
            continue
        f, name = junit.pytest_key(c, modules)
        if not f:
            unmapped.add(f"{c.classname}::{c.name}")
            continue
        file_id, test_id = f"{prefix}:{f}", f"{prefix}:{f}::{name}"
        mapped.add(test_id if name != "<collection>" and test_id in ids else file_id)
    return mapped, sorted(unmapped)


def is_caught(test_id: str, selected: set[str]) -> bool:
    """Its id, its file, or any selected test in its file is in the plan."""
    return test_id.split("::")[0] in {s.split("::")[0] for s in selected}


def _at_head(cfg: Config, base: str, head: str, edition: str) -> tuple[set[str], list[dict[str, Any]]]:
    with collect.worktree(cfg.root, head) as tree:
        wcfg = config.load(root=tree, engine_root=cfg.engine_root)
        changeset, _ = collect.collect(wcfg, base, head, mode="range")
        rows = catalog.build(wcfg)
        plan = impact.compute(wcfg, changeset, rows, profile="standard", scope=edition)
        return set(plan["floor"]["tests"]), rows


class MergeConflict(Exception):
    """A pull_request run whose head no longer merges cleanly into its base."""


def plan_revs(root: Path, run: dict[str, Any], default: str) -> tuple[str, str]:
    """(base, head) to plan on. A pull_request run tested refs/pull/N/merge, so its head is the merge of
    the PR head into the base tip; anything else tested its head against the default branch."""
    collect.git(root, "fetch", "--no-tags", "-q", "origin", *filter(None, [run["head"], run.get("base_sha")]))
    if run.get("event") != "pull_request":
        return collect.git(root, "merge-base", run["head"], f"origin/{default}").strip(), run["head"]
    # shortcut: the base sha GitHub reports for the run, which may differ from the tip it merged; a later base push can shift it.
    tip = run.get("base_sha") or collect.rev(root, f"origin/{run.get('base_ref') or default}")
    r = subprocess.run(["git", "merge-tree", "--write-tree", tip, run["head"]], cwd=root, capture_output=True, text=True)
    if r.returncode == 1:
        raise MergeConflict(f"merge conflict with {tip[:12]}")
    if r.returncode:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    merge = subprocess.run(["git", "commit-tree", r.stdout.split()[0], "-p", tip, "-p", run["head"], "-m", "qa shadow merge"],
                           cwd=root, check=True, capture_output=True, text=True, env={**os.environ, **branchstore.IDENT})
    return tip, merge.stdout.strip()


def selected_by_floor(cfg: Config, base: str, head: str, edition: str) -> set[str]:
    return _at_head(cfg, base, head, edition)[0]


def compare(cfg: Config, base: str, head: str, junit_files: list[tuple[str, Path]], *, workflow: str,
            run_id: str | None, edition: str, ts: int | None = None, key: str | None = None) -> dict[str, Any]:
    base, head = collect.rev(cfg.root, base), collect.rev(cfg.root, head)
    selected, rows = _at_head(cfg, base, head, edition)
    caught, missed, unmapped = set(), set(), set()
    for prefix, path in junit_files:
        ids, labels = failures(junit.read(Path(path)), rows, prefix)
        for i in ids:
            (caught if is_caught(i, selected) else missed).add(i)
        unmapped.update(labels)
    return {"ts": int(time.time()) if ts is None else ts, "workflow": workflow, "run_id": run_id, "head": head,
            "base": base, "edition": edition, "key": key, "caught": sorted(caught),
            "missed": sorted(missed)[:MAX_LISTED], "unmapped": sorted(unmapped)[:MAX_LISTED],
            "missed_count": len(missed), "unmapped_count": len(unmapped)}


def _day(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def enforcement(records: list[dict[str, Any]], today: dt.date, days: int = 14, target: float = 0.98,
                min_failures: int = 20) -> dict[str, Any]:
    records = [r for r in records if isinstance(r.get("ts"), (int, float))]
    since = min((_day(r["ts"]) for r in records), default=None)
    cutoff = dt.datetime.combine(today - dt.timedelta(days=days), dt.time.min, tzinfo=dt.timezone.utc).timestamp()
    seen: dict[tuple[str, str], bool] = {}
    extra: dict[str, int] = {}
    for r in records:
        if r["ts"] < cutoff:
            continue
        # Every push to a PR re-runs the same tests; one failure per PR (or branch) is one data point.
        key = r.get("key") or r.get("head")
        for tid in r.get("caught", []):
            seen[(key, tid)] = True
        for tid in [*r.get("missed", []), *r.get("unmapped", [])]:
            seen.setdefault((key, tid), False)
        # shortcut: ids past the list cap count as misses once per key, at the largest overflow seen.
        over = sum(max(0, int(r.get(f"{k}_count") or 0) - len(r.get(k, []))) for k in ("missed", "unmapped"))
        extra[key] = max(extra.get(key, 0), over)
    n, caught = len(seen) + sum(extra.values()), sum(seen.values())
    recall = round(caught / n, 4) if n else None
    missed = sorted({tid for (_, tid), ok in seen.items() if not ok})[:20]
    if since is None or (today - since).days < days:
        status, reason = "insufficient", f"needs {days} days of shadow data; have {(today - since).days if since else 0}"
    elif n < min_failures:
        status, reason = "insufficient", f"needs {min_failures} failures in the last {days} days; have {n}"
    elif caught / n + 1e-9 >= target:
        status, reason = "ready", f"recall {recall} >= {target} over {n} failures in the last {days} days"
    else:
        status, reason = "not_ready", f"recall {recall} < {target} over {n} failures in the last {days} days"
    return {"ready": status == "ready", "status": status, "recall": recall, "failures": n, "caught": caught,
            "missed": missed, "since": since.isoformat() if since else None, "reason": reason,
            "families": FAMILIES, "population": POPULATION}


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _gh(args: list[str]) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {r.stderr.strip() or f'exit {r.returncode}'}")
    return r.stdout


def fetch(repo: str, workflows: list[str], since: dt.datetime, dest: Path) -> list[dict[str, Any]]:
    """Failed runs of each workflow since `since`, with their JUnit files downloaded under dest/<run id>."""
    out = []
    for wf in workflows:
        # shortcut: the newest 200 failed runs per workflow; page with --created if a day ever has more.
        try:
            runs = json.loads(_gh(["run", "list", "-R", repo, "--workflow", wf, "--status", "failure",
                                   "--limit", "200", "--json", "databaseId,headSha,createdAt"]) or "[]")
        except (RuntimeError, OSError, ValueError) as err:
            print(f"shadow: {wf}: cannot list failed runs: {err}", file=sys.stderr)
            continue
        for r in runs:
            if dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")) < since:
                continue
            run_id = str(r["databaseId"])
            try:
                detail = json.loads(_gh(["api", f"repos/{repo}/actions/runs/{run_id}"]))
                fork = detail["head_repository"]["full_name"] != detail["repository"]["full_name"]
            except (RuntimeError, OSError, ValueError, KeyError, TypeError) as err:
                print(f"shadow: run {run_id}: cannot read the run: {err}", file=sys.stderr)
                continue
            if fork:  # a fork controls its own artifacts
                print(f"shadow: run {run_id}: skipped, its head is in a fork", file=sys.stderr)
                continue
            pr = (detail.get("pull_requests") or [{}])[0]
            base = pr.get("base") or {}
            target = dest / run_id
            files: list[tuple[str, Path]] = []
            for pattern, prefix in WORKFLOW_ARTIFACTS[wf]:
                try:
                    _gh(["run", "download", run_id, "-R", repo, "-p", pattern, "-D", str(target)])
                except RuntimeError as err:  # no matching artifact, or it expired
                    print(f"shadow: run {run_id}: {err}", file=sys.stderr)
                    continue
                # gh puts each downloaded artifact in a directory named after it.
                dirs = sorted(d for d in target.iterdir() if d.is_dir() and fnmatch.fnmatch(d.name, pattern)) if target.is_dir() else []
                files += [(prefix, p) for d in dirs for p in sorted(d.rglob("*.xml"))]
            if files:
                out.append({"run_id": run_id, "workflow": wf, "head": r["headSha"], "junit": files,
                            "event": detail.get("event"), "base_sha": base.get("sha"), "base_ref": base.get("ref"),
                            "key": f"pr-{pr['number']}" if pr.get("number") else f"branch-{detail.get('head_branch')}"})
    return out

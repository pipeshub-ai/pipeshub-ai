"""Find the commit that broke a test in a range: flake check, narrow, bisect.

Re-runs only the failing test ids. Each probe runs in a throwaway git worktree
so the caller's checkout is never touched.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from qa import catalog, collect, config, impact

Probe = Callable[[Path, list[str]], dict[str, str]]


def first_parent_commits(root: Path, base: str, head: str) -> list[str]:
    out = collect.git(root, "rev-list", "--first-parent", "--reverse", f"{base}..{head}")
    return out.split()


def _commit_selects(cfg: config.Config, sha: str, failing: set[str], rows: list[dict[str, Any]]) -> bool:
    base = collect.parent_or_empty(cfg.root, sha)
    changeset, _ = collect.collect(cfg, base, sha, mode="range")
    plan_input = impact.compute(cfg, changeset, rows, profile="conservative")
    return bool(failing & set(plan_input["floor"]["tests"]))


def failing_ids(outcomes: dict[str, str]) -> set[str]:
    return {i for i, o in outcomes.items() if o in ("fail", "error")}


def worktree_probe(run_ids: Callable[[Path, list[str]], dict[str, str]]) -> Probe:
    """Wrap a runner so it executes against a detached worktree at a commit."""
    def probe(root: Path, sha_and_ids: list[str]) -> dict[str, str]:
        sha, ids = sha_and_ids[0], sha_and_ids[1:]
        with collect.worktree(root, sha) as tmp:
            return run_ids(tmp, ids)
    return probe


def search(
    cfg: config.Config, base: str, head: str, tests: list[str], probe: Probe,
    max_steps: int = 8, flake_runs: int = 2,
) -> dict[str, Any]:
    root = cfg.root
    head_sha = collect.rev(root, head)
    log: list[str] = []

    still = set(tests)
    for n in range(flake_runs):
        outcomes = probe(root, [head_sha, *sorted(still)])
        still &= failing_ids(outcomes)
        log.append(f"flake check {n + 1}/{flake_runs} at {head_sha[:10]}: {len(still)} still failing")
        if not still:
            return {"result": "flaky", "tests": tests, "log": log}

    commits = first_parent_commits(root, base, head_sha)
    rows = catalog.build(cfg)
    candidates = [c for c in commits if _commit_selects(cfg, c, still, rows)]
    log.append(f"{len(candidates)} of {len(commits)} commits touch what the failing tests cover")
    if not candidates:
        candidates = commits
        log.append("no commit maps to the failing tests; bisecting the whole range")
    if len(candidates) == 1:
        return {"result": "exact", "culprit": candidates[0], "failing": sorted(still), "log": log}

    lo, hi, steps = 0, len(candidates) - 1, 0
    while lo < hi and steps < max_steps:
        mid = (lo + hi) // 2
        outcomes = probe(root, [candidates[mid], *sorted(still)])
        broken = bool(failing_ids(outcomes) & still)
        log.append(f"probe {candidates[mid][:10]}: {'fails' if broken else 'passes'}")
        if broken:
            hi = mid
        else:
            lo = mid + 1
        steps += 1
    if lo == hi:
        return {"result": "exact", "culprit": candidates[lo], "failing": sorted(still), "log": log}
    return {"result": "candidates", "candidates": candidates[lo : hi + 1], "failing": sorted(still), "log": log}

"""The result check of a label-gated heavy workflow (.github/actions/heavy-result).

GitHub reports a skipped job as a success, and the newest run of a workflow on a commit is the one its
checks come from. So a pull request event the gate does not act on (an unrelated label) would start a
run whose skipped heavy jobs replace a failed result. This check is what branch protection requires
instead of the heavy jobs:

- on an event the gate acts on (`HEAVY=true`), it fails when a job it needs failed or was cancelled;
- on any other event, it reports the result check of this workflow's latest earlier run on the same
  commit, waiting while that run is still going, and passes when there is none (a pull request with no
  label may merge).

Env: GH_TOKEN (actions: read), GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_API_URL, HEAVY, NEEDS
(toJSON(needs)), RESULT_JOB (this job's name), HEAD_SHA, WAIT_MINUTES: how long to wait, polling every
30 s, for an earlier run still going (the workflow's longest heavy-job timeout, as no run outlives it); past
that it fails and asks for a re-run.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Any, Callable

PASS = {"success", "skipped", "neutral"}


def this_run(needs: dict[str, Any]) -> tuple[bool, str]:
    bad = sorted(job for job, need in needs.items() if need.get("result") in ("failure", "cancelled"))
    return (not bad, f"failed or cancelled: {', '.join(bad)}" if bad else "every heavy job passed or was skipped")


def earlier(runs: list[dict[str, Any]], run_id: int) -> dict[str, Any] | None:
    """The latest run on this commit started before this one (run ids only grow)."""
    before = [r for r in runs if r["id"] < run_id]
    return max(before, key=lambda r: r["id"]) if before else None


def mirror(status: str, conclusion: str | None) -> tuple[bool, str] | None:
    """The earlier result as this run's verdict; None while it is still running."""
    if status != "completed":
        return None
    return (conclusion in PASS, f"the earlier run's result: {conclusion}")


def decide(
    heavy: bool, needs: dict[str, Any], run_id: int,
    list_runs: Callable[[], list[dict[str, Any]]],
    result_of: Callable[[dict[str, Any]], tuple[str, str | None]],
    wait_seconds: float, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    if heavy:
        return this_run(needs)
    deadline = clock() + wait_seconds
    while True:
        prev = earlier(list_runs(), run_id)
        if prev is None:
            return (True, "no earlier run on this commit")
        verdict = mirror(*result_of(prev))
        if verdict is not None:
            return verdict[0], f"run {prev['id']}: {verdict[1]}"
        if clock() >= deadline:
            return (False, f"run {prev['id']} is still running after the wait; re-run this job once it finishes")
        sleep(30)


def _api(path: str) -> Any:
    req = urllib.request.Request(
        f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}/{path}",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    repo, run_id = os.environ["GITHUB_REPOSITORY"], int(os.environ["GITHUB_RUN_ID"])
    workflow_id = _api(f"repos/{repo}/actions/runs/{run_id}")["workflow_id"]
    sha, job = os.environ["HEAD_SHA"], os.environ["RESULT_JOB"]

    def list_runs() -> list[dict[str, Any]]:
        return _api(f"repos/{repo}/actions/workflows/{workflow_id}/runs?head_sha={sha}&per_page=100")["workflow_runs"]

    def result_of(run: dict[str, Any]) -> tuple[str, str | None]:
        jobs = _api(f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100")["jobs"]
        mine = [j for j in jobs if j["name"] == job]
        # A run from before this check existed: its own conclusion stands in.
        source = mine[0] if mine else run
        return source["status"], source.get("conclusion")

    ok, why = decide(
        os.environ["HEAVY"] == "true", json.loads(os.environ.get("NEEDS") or "{}"), run_id,
        list_runs, result_of, float(os.environ.get("WAIT_MINUTES") or 360) * 60,
    )
    print(why if ok else f"::error::{why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

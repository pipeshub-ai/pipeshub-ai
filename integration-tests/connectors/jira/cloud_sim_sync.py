# pyright: ignore-file

"""Helpers of the round-2 Jira Cloud sync, deletion, project and failure files: one faulted run with the safety checks of
``cloud_sim_campaign.py`` around it, and the site changes those files make (many projects, many issues, a project's
records named by their external ids)."""

from __future__ import annotations

import logging
from typing import Any

from connectors.jira.cloud_sim_world import (
    COMMENT_PREFIX,
    FILE_PREFIX,
    SYNC_ACCOUNT,
    JiraCloudWorld,
    _rule,
    succeeded,
)

logger = logging.getLogger("jira-cloud-sim-sync2")

API = r"^/rest/api/3"
SEARCH_RX = API + r"/search/jql$"
BULK_RX = API + r"/issue/bulkfetch$"
AUDIT_RX = API + r"/auditing/record$"
PROJECT_SEARCH_RX = API + r"/project/search$"
PROJECT_ONE_RX = API + r"/project/[^/]+$"
ISSUE_ONE_RX = API + r"/issue/[^/]+$"
MYSELF_RX = API + r"/myself$"
PERSISTENT = 500
FREE_PLAN_BODY = "Audit log is not available: all of its Jira Cloud products are on Free plans."


def free_plan_audit(**trigger: float) -> dict[str, Any]:
    """The audit log refused as Jira does when every product of the site is on a Free plan (403, and the body says so)."""
    return _rule({"type": "status", "code": 403, "message": FREE_PLAN_BODY}, AUDIT_RX, None, trigger)


def shaped(path_regex: str, *ops: dict[str, Any], **trigger: float) -> dict[str, Any]:
    """A 200 answer whose body is changed (``$`` replaces the whole body): the vendor never sends it, a proxy or a bug may."""
    return _rule({"type": "shape", "ops": list(ops)}, path_regex, None, trigger)


def ids_of(w: JiraCloudWorld, *, project: str | None = None, keys: set[str] | None = None) -> set[str]:
    """The external ids (tickets, files, restricted comments) the oracle holds for a project or for issue keys."""
    out: set[str] = set()
    for tid, t in w.sim.expected()["ticket_index"].items():
        key = t["key"]
        if (project is not None and key.rsplit("-", 1)[0] != project) or (keys is not None and key not in keys):
            continue
        out.add(tid)
        out |= {f"{FILE_PREFIX}{f['id']}" for f in t["files"]}
        out |= {f"{COMMENT_PREFIX}{c['id']}" for c in t["comments"] if c["restricted"]}  # the others are in the ticket's content
    return out


def make_projects(w: JiraCloudWorld, count: int, prefix: str = "Z") -> list[str]:
    """``count`` new projects (Jira's own API), the first with no issue; returns their keys."""
    lead = w.sim.users()[0]["account_id"]
    keys = []
    for i in range(count):
        key = f"{prefix}{i:02d}".replace("0", "A").replace("1", "B").replace("2", "C").replace("3", "D").replace("4", "E") \
            .replace("5", "F").replace("6", "G").replace("7", "H").replace("8", "I").replace("9", "J")
        w.sim.rest("POST", "/project", {"key": key, "name": f"Project {key}", "projectTypeKey": "software",
                                        "leadAccountId": lead})
        keys.append(key)
    return keys


def make_issues(w: JiraCloudWorld, project: str, count: int, summary: str = "Added by the test",
                issue_type: str = "Task") -> list[str]:
    """``count`` issues in ``project`` through Jira's own API; returns their keys."""
    out = []
    for i in range(count):
        made = w.sim.rest("POST", "/issue", {"fields": {"project": {"key": project}, "summary": f"{summary} {i}",
                                                       "issuetype": {"name": issue_type}}})
        out.append(made["key"])
    return out


def end_faults(w: JiraCloudWorld) -> int:
    """Remove the faults (failing when none fired); the world's own check leaves out what a fault answered."""
    return w.clear_faults()


def calls(w: JiraCloudWorld) -> dict[str, int]:
    return w.sim.paths_called()


async def run_case(w: JiraCloudWorld, rule: dict[str, Any] | list[dict[str, Any]], label: str, *, full: bool = False,
                   when: str = "sync", gone: set[str] | frozenset[str] = frozenset(), fires: bool = True,
                   before: dict[str, set[str]] | None = None) -> tuple[str | None, int]:
    """One faulted run, then the safety checks, then a clean run that must equal Jira.

    SAFETY (never excused): the fault fired (``fires``); no record and no access the oracle still holds is lost (``gone``:
    external ids Jira removed, which a run may or may not have removed yet); nobody reads what Jira does not show them (no
    LEAK, DUPLICATE or misfiled record), whatever the outcome; the next clean sync succeeds and equals Jira.
    Returns (the faulted run's outcome, how many times the fault fired)."""
    rules = rule if isinstance(rule, list) else [rule]
    before = before if before is not None else await w.holdings()
    w.faults(*rules)
    outcome = await w.restart() if when == "init" else await w.sync(full=full)
    if fires:
        fired = end_faults(w)
    else:
        fired = len(w.sim.call("GET", "/_sim/faults/log") or [])
        w.sim.call("DELETE", "/_sim/faults")
        w._fault_rules = []
    after = await w.holdings()
    lost = {who: sorted(before[who] - after.get(who, set()) - set(gone)) for who in before}
    lost = {who: v for who, v in lost.items() if v}
    assert not lost, f"{label}: a run lost records or access Jira still gives: {lost}"
    problems = await w.differences()
    bad = [p for p in problems if str(p["type"]).startswith(("LEAK", "DUPLICATE", "PARENT"))
           or (p["type"] == "STALE" and p["record"] not in gone)
           or p["type"] == "SIM_UNMODELED"]
    assert not bad, f"{label}: the run ({outcome}) shows what Jira does not: {bad[:10]}"
    assert succeeded(await w.sync()), f"{label}: the next clean sync did not succeed"
    await w.assert_matches_source(f"{label}: after the refusal ended")
    return outcome, fired


async def clean(w: JiraCloudWorld, label: str, *, full: bool = False) -> None:
    """A clean run that succeeds and equals Jira."""
    assert succeeded(await w.sync(full=full)), f"{label}: the sync did not succeed"
    await w.assert_matches_source(label)


def sync_account_only(w: JiraCloudWorld) -> str:
    return SYNC_ACCOUNT


def gaps(w: JiraCloudWorld, field_: str, n: int = 1) -> list[int]:
    """Times in the ``n`` widest gaps between two issues' ``field_`` times (``created_ms`` or ``updated_ms``), so a minute's
    rounding cannot move an issue across a date filter."""
    import itertools

    times = sorted(t[field_] for t in w.sim.expected()["ticket_index"].values())
    found = sorted(((b - a, (a + b) // 2) for a, b in itertools.pairwise(times)), reverse=True)[:n]
    assert found[-1][0] > 60_000, "no gap of a minute between two issues to put a date filter in"
    return sorted(at for _width, at in found)


def child_under_another_projects_epic(w: JiraCloudWorld) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move a standard issue under an epic of another project (the ``issue`` / ``parent`` op); return the issue and that epic
    (the oracle's rows). The seeded site has no such pair, and a sub-task is always in its parent's project. Types come from
    Jira's own answer for each issue (the oracle's rows do not carry them)."""
    index = w.sim.expected()["ticket_index"]
    kind: dict[str, Any] = {}

    def of(tid: str) -> dict[str, Any]:
        if tid not in kind:
            kind[tid] = w.sim.rest("GET", f"/issue/{tid}", fields="issuetype")["fields"]["issuetype"]
        return kind[tid]

    for child in index.values():
        old = index.get(str(child["parent_id"]))
        if not old or old["project_id"] != child["project_id"] or of(str(child["key"]))["subtask"]:
            continue
        for tid, t in index.items():
            if t["project_id"] != child["project_id"] and of(tid)["id"] == of(str(child["parent_id"]))["id"]:
                w.mutate({"op": "issue", "issue": child["key"], "parent": t["key"]})
                return child, t
    raise AssertionError("no standard issue could be moved under an epic of another project")

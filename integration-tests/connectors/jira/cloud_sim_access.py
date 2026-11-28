# pyright: ignore-file

"""Helpers of the Jira Cloud access suites: who reads what in Jira (oracle), who reads what in PipesHub, and the bounds a
refused read must keep.

A read that Jira refuses (401, 403, 404, 429, 5xx, a dropped or cut-short answer, an empty one) leaves the stored access
as it was. So after a change of access and one run that could not read it, each person reads something between what Jira
showed before the change and after it:

  nobody reads more than Jira shows:   reads(PipesHub) is a subset of reads(Jira before) | reads(Jira after)
  nobody loses what Jira still shows:  reads(Jira before) & reads(Jira after) is a subset of reads(PipesHub)

and the next clean run equals Jira.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.jira.cloud_sim_world import (
    COMMENT_PREFIX,
    JiraCloudWorld,
    _project_key,
    drop,
    fault,
    malformed,
    succeeded,
    wanted_records,
)

logger = logging.getLogger("jira-cloud-sim-access")

API = r"^/rest/api/3"
PERSISTENT = 500

# the kind of refusal -> a fault rule for a path regex (``empty`` needs the answer to replace)
KINDS = ("401", "403", "404", "429", "500", "malformed", "drop", "empty")


def rule(kind: str, rx: str, empty: Any = None, **trigger: float) -> dict[str, Any]:
    if kind in ("401", "403", "404", "429", "500"):
        return fault(rx, int(kind), **trigger)
    if kind == "drop":
        return drop(rx, **trigger)
    if kind == "malformed":
        return malformed(rx, **trigger)
    if kind == "empty":
        out = {"match": {"path_regex": rx}, "effect": {"type": "shape", "ops": [{"path": "$", "value": empty}]}}
        if trigger:
            out["trigger"] = trigger
        return out
    raise ValueError(kind)


PAGE_EMPTY = {"values": [], "isLast": True, "total": 0, "startAt": 0, "maxResults": 50}


def oracle(w: JiraCloudWorld) -> dict[str, set[str]]:
    """What Jira lets each probed person read now: e-mail -> external ids of tickets and files."""
    expected = w.sim.expected()
    access = w.probes()
    wanted = wanted_records(expected, access, w.scope, None, comments=False)
    return {email: {ext for ext, want in wanted.items() if email in want.readers} for email in access}


async def actual(w: JiraCloudWorld) -> dict[str, set[str]]:
    held = await w.holdings()
    return {e: {x for x in held.get(e, set()) if not x.startswith(COMMENT_PREFIX)} for e in w.people}


def within_bounds(before: dict[str, set[str]], after: dict[str, set[str]], now: dict[str, set[str]], label: str,
                  *, between: list[dict[str, set[str]]] | None = None, losses_ok: bool = False) -> None:
    seen = [before, after, *(between or [])]
    union = {e: set().union(*(o.get(e, set()) for o in seen)) for e in now}
    inter = {e: set.intersection(*(set(o.get(e, set())) for o in seen)) for e in now}
    more = {e: sorted(now[e] - union[e])[:5] for e in now}
    less = {e: sorted(inter[e] - now[e])[:5] for e in now}
    more = {e: v for e, v in more.items() if v}
    less = {e: v for e, v in less.items() if v}
    assert not more, f"{label}: people read what Jira shows them neither before nor after the change: {more}"
    assert losses_ok or not less, f"{label}: people lost what Jira still shows them: {less}"


def pick(w: JiraCloudWorld) -> tuple[str, str, str]:
    """(a project, a person who reads nothing of it, a second such person)."""
    for project in w.projects():
        who = w.outsiders(project)
        if len(who) >= 2:
            return project, who[0], who[1]
    raise AssertionError("no project has two outsiders")


async def refused(w: JiraCloudWorld, change: list[dict[str, Any]], fault_rule: dict[str, Any], label: str, *,
                  full: bool, before: dict[str, set[str]] | None = None, first: bool = False, clean: bool = True,
                  window: list[dict[str, set[str]]] | None = None, losses_ok: bool = False) -> str | None:
    """One run with one read refused after a change of access; checks the bounds and (``clean``) that the next clean run
    equals Jira. Several runs may follow one another without a clean run between: pass the same ``window`` list, so each run
    is bounded by every state Jira had since the last clean run. Returns the run's outcome."""
    window = window if window is not None else []
    if not window:
        window.append(before if before is not None else oracle(w))
    if change:
        w.mutate(*change)
    window.append(oracle(w))
    w.faults(fault_rule)
    outcome = await w.enable() if first else await w.sync(full=full)
    w.clear_faults()  # fails when the fault never fired
    within_bounds(window[0], window[-1], await actual(w), f"{label} (outcome {outcome})", between=window[1:-1], losses_ok=losses_ok)
    if clean:
        assert succeeded(await w.sync(full=full)), f"{label}: the next clean run did not succeed"
        await w.assert_matches_source(f"{label}: after Jira answered again")
        window.clear()
    return outcome

# pyright: ignore-file

"""Helpers of the round 4 access suite: the organisation gate (opened, aged, read back) on a site that is public."""

from __future__ import annotations

from typing import Any

from connectors.jira.cloud_sim_access import pick
from connectors.jira.cloud_sim_world import OUTSIDER_EMAIL, JiraCloudWorld, succeeded

GATE_KEY = "anonymous_gate"
DAY_MS = 24 * 60 * 60 * 1000
ISSUE_BULK = r"^/rest/api/3/issue/bulkfetch$"


async def gate_state(w: JiraCloudWorld) -> dict[str, Any]:
    """The connector's saved confirmation of the public projects (sync point ``anonymous_gate``); {} when none."""
    return dict(await w.graph.get_sync_point(w.connector_id, GATE_KEY) or {})


async def confirmed_at(w: JiraCloudWorld) -> int:
    return int((await gate_state(w)).get("confirmed_at") or 0)


async def age_gate(w: JiraCloudWorld, by_ms: int) -> int:
    """Move the saved confirmation back in time by ``by_ms`` (the only clock the suite has); returns the new value."""
    state = await gate_state(w)
    assert int(state.get("confirmed_at") or 0), "the gate was never confirmed: nothing to age"
    state["confirmed_at"] = int(state["confirmed_at"]) - by_ms
    assert await w.graph.upsert_sync_point(state["syncPointKey"], state, "syncPoints")
    assert await confirmed_at(w) == state["confirmed_at"], "the saved confirmation did not move"
    return state["confirmed_at"]


async def outsider_reads(w: JiraCloudWorld) -> set[str]:
    return set((await w.holdings()).get(OUTSIDER_EMAIL, set()))


async def open_gate(w: JiraCloudWorld, projects: int = 1, *, extra: list[dict[str, Any]] | None = None) -> list[str]:
    """A public site, ``projects`` projects granting ``anyone``, a first sync, and PROOF the gate is open: the confirmation is
    saved and the PipesHub account that has no Jira account reads every issue Jira shows a signed-out visitor. Returns the keys."""
    w.sim.reset()
    w.sim.switches(anonymous_access=True, anonymous_refusal="", anonymous_refusal_times=0)
    first, *_ = pick(w)
    keys = [first, *[p for p in w.projects() if p != first]][:projects]
    prepare = [{"op": "grant", "project": k, "holder": "anyone"} for k in keys] + list(extra or [])
    assert succeeded(await w.start(reset=False, prepare=prepare))
    if not await confirmed_at(w):
        assert succeeded(await w.sync())
    await assert_open(w, f"{projects} public project(s)")
    return keys


async def assert_open(w: JiraCloudWorld, label: str) -> None:
    assert await confirmed_at(w), f"{label}: the gate holds no confirmation"
    visitors = w.visitor_ids()
    assert visitors, f"{label}: Jira shows signed-out visitors nothing"
    missing = visitors - await outsider_reads(w)
    assert not missing, f"{label}: the gate is not open, the organisation misses {sorted(missing)[:5]}"


async def assert_closed(w: JiraCloudWorld, seen_by_visitors: set[str], label: str) -> None:
    """The organisation reads none of what the gate had given it (the linked accounts keep what Jira gives them)."""
    assert not await confirmed_at(w), f"{label}: a confirmation is still saved"
    held = await outsider_reads(w)
    assert not (held & seen_by_visitors), f"{label}: the organisation still reads {sorted(held & seen_by_visitors)[:5]}"


async def assert_no_gain(w: JiraCloudWorld, label: str) -> None:
    """No record is stored twice and nobody reads what Jira does not show them; people may lose what the gate had given them."""
    problems = [p for p in await w.differences() if not str(p["type"]).startswith("DENIED")]
    assert not problems, f"{label}: PipesHub gives access Jira does not: {problems[:10]}"

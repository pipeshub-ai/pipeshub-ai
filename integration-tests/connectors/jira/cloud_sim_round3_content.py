# pyright: ignore-file

"""Helpers of the round-3 content suite: the ticket as an agent reads it with its live Jira fields, the fields the
simulator holds for an issue (the oracle of a live line), and the ancestor stubs of a sync.

The simulator serves, besides the catalogue, components, versions, environment, time tracking and Jira Software's Epic Link,
Epic Name and Sprint fields, which a test sets through ``mutate`` op ``issue_field`` (simulator ``jira_cloud/mutate.py``).
"""

from __future__ import annotations

import itertools
from typing import Any

from connectors.jira.cloud_sim_content import read_record, wait_for
from connectors.jira.cloud_sim_world import JiraCloudWorld

LIVE_HEAD = "Ticket Information:"
EPIC_LINK, EPIC_NAME, SPRINT = "customfield_10060", "customfield_10061", "customfield_10062"
BULKFETCH_URL = r"^/rest/api/3/issue/bulkfetch$"


def set_field(w: JiraCloudWorld, key: str, field: str, value: Any) -> None:
    w.mutate({"op": "issue_field", "issue": key, "field": field, "value": value})


def jira_fields(w: JiraCloudWorld, key: str, *names: str) -> dict[str, Any]:
    """What Jira (the simulator) answers for these fields of the issue: the oracle of a live line."""
    return w.sim.rest("GET", f"/issue/{key}", fields=",".join(names))["fields"]


async def read_ticket(w: JiraCloudWorld, record_id: str, *, need: str = LIVE_HEAD, timeout: float = 420) -> str:
    """The ticket as the agent tool reads it, once its record is indexed; waits until ``need`` is in the text."""
    await w.wait_indexed(record_id, timeout=timeout)
    out: dict[str, str] = {}

    async def read():
        resp = read_record(w, record_id)
        if resp.status_code != 200:
            return None
        out["text"] = str(resp.json().get("content") or "")
        return need in out["text"] or None

    try:
        await wait_for(read, "the ticket's content", timeout=timeout, interval=5)
    except AssertionError as exc:
        raise AssertionError(f"{exc}; it reads {out.get('text', '')[:1500]!r}") from exc
    return out["text"]


def line(text: str, label: str) -> str | None:
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith(f"* {label}:"):
            return s
    return None


def a_readable_issue(w: JiraCloudWorld, by_ext: dict[str, str], *, skip: set[str] = frozenset(), plain: bool = True) -> tuple[str, str]:
    """(issue id, key) of an issue the PipesHub reader may read that is not an epic (an epic's content is not indexed on this
    stack, so its ticket cannot be read) and not in ``skip``."""
    index = w.sim.expected()["ticket_index"]
    for tid, t in sorted(index.items(), key=lambda kv: int(kv[0])):
        if tid not in by_ext or t["key"] in skip:
            continue
        if plain and jira_fields(w, t["key"], "issuetype")["issuetype"]["name"] == "Epic":
            continue
        return tid, t["key"]
    raise AssertionError("no readable issue")


def child_of_older_parent(w: JiraCloudWorld) -> tuple[dict, dict, int]:
    """(child row, parent row, time between their creations) of the oracle: a child in its parent's project created at
    least two minutes after it, so a ``created`` filter can sit between them."""
    index = w.sim.expected()["ticket_index"]
    best = None
    for child in index.values():
        parent = index.get(str(child["parent_id"])) if child["parent_id"] else None
        if not parent or parent["project_id"] != child["project_id"]:
            continue
        gap = child["created_ms"] - parent["created_ms"]
        if gap > 120_000 and (best is None or gap > best[2]):
            best = (child, parent, gap)
    if not best:
        raise AssertionError("no child was created two minutes after its parent in the same project")
    return best


async def graph_rows(w: JiraCloudWorld) -> list[dict[str, Any]]:
    """Every record the connector holds, stubs included."""
    return list(await w.graph.fetch_records_by_type(w.connector_id, "", scoped=True) or [])


async def stub_of(w: JiraCloudWorld, issue_id: str) -> dict[str, Any] | None:
    return next((r for r in await graph_rows(w) if str(r.get("externalRecordId")) == str(issue_id)
                 and r.get("isPlaceholder") and not r.get("isDeleted")), None)


def pairs(items):
    return list(itertools.pairwise(items))

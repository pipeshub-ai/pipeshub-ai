# pyright: ignore-file

"""Helpers of the round 3 access suite: a project with a security level, one issue under it, and the people a level's
custom-field holders can name (people who read the project but not that issue through any other holder)."""

from __future__ import annotations

from typing import Any

import pytest
from connectors.jira.cloud_sim_world import CREATOR_EMAIL, JiraCloudWorld, succeeded

USER_PICKERS = ("customfield_10052", "customfield_10053")    # single, multiple
GROUP_PICKERS = ("customfield_10051", "customfield_10050")   # single, multiple


def ticket_id(w: JiraCloudWorld, key: str) -> str:
    return next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == key)


def holder(project: str, level: str, kind: str, parameter: str = "", present: bool = True) -> dict[str, Any]:
    op = {"op": "level_member", "project": project, "level": level, "holder": kind, "present": present}
    if parameter:
        op["parameter"] = parameter
    return op


def picker(issue: str, field: str, value: Any) -> dict[str, Any]:
    return {"op": "picker", "issue": issue, "field": field, "value": value}


def readers(w: JiraCloudWorld, tid: str) -> set[str]:
    """The linked people Jira lets read ticket ``tid`` (not the connector's creator)."""
    return {e for e in w.readers_now()[tid] if e != CREATOR_EMAIL}


class Leveled:
    """One issue of a project with a level scheme, put under its first level, with the people the level does not admit."""

    def __init__(self, w: JiraCloudWorld) -> None:
        self.w = w
        w.sim.reset()
        self.project, levels = w.leveled_project()
        self.level = levels[0]["name"]
        self.issue = w.issue_keys(self.project)[0]
        self.tid = ticket_id(w, self.issue)
        self.level_id = levels[0]["id"]
        self.scheme = w.sim.rest("GET", f"/project/{self.project}/issuesecuritylevelscheme")["id"]
        w.mutate({"op": "security", "issue": self.issue, "level": self.level})

    def candidates(self) -> list[str]:
        """People who read the project and not this issue (the level keeps them out), active and licensed."""
        w = self.w
        inside = readers(w, self.tid)
        projectwide = w.project_readers(self.project) - {CREATOR_EMAIL}
        found = [e for e in sorted(projectwide - inside) if e in w.emails() and w.probes()[e].get("licensed")]
        if len(found) < 4:
            pytest.fail(f"project {self.project} leaves only {len(found)} people outside the level of {self.issue}")
        return found

    async def start(self, extra: list[dict[str, Any]]) -> None:
        w = self.w
        assert succeeded(await w.start(reset=False, prepare=extra))
        await w.assert_matches_source("after the first sync")

    async def sync_and_compare(self, label: str, *, full: bool = False) -> None:
        w = self.w
        assert succeeded(await w.sync(full=full)), f"{label}: the run did not succeed"
        await w.assert_matches_source(label)

    def expect(self, who: str, reads: bool, label: str) -> None:
        """The oracle (Jira) says ``who`` reads the issue, or does not: the scenario is the one it claims to be."""
        now = readers(self.w, self.tid)
        assert (who in now) is reads, f"{label}: Jira {'does not show' if reads else 'shows'} {who} the issue"


def shape(rx: str, *ops: dict[str, Any], **trigger: float) -> dict[str, Any]:
    """A fault that lets the answer through with JSON fields set (``value``) or deleted (``delete``)."""
    out: dict[str, Any] = {"match": {"path_regex": rx}, "effect": {"type": "shape", "ops": list(ops)}}
    if trigger:
        out["trigger"] = trigger
    return out


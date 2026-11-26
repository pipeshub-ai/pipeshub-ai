# pyright: ignore-file

"""A fault campaign over one connector: each case changes the site, refuses one endpoint, runs a sync and checks it.

Two kinds of assertion, kept apart:

SAFETY, never excused: the fault fired; whatever the outcome, no record and no access is lost; a run that did not
succeed gives nobody access Jira does not give them; a run that did succeed leaves no one reading what
Jira does not show them (no LEAK, STALE, DUPLICATE or misfiled record); and once Jira answers again the next clean sync
equals Jira (``finish``).

OUTCOME: a run that could not read what it needed must not report ``succeeded``. Some runs still do (the change they
could not read stays unread, nothing is lost or gained): the cases that do today are listed in ``PARTIAL_SUCCESS`` of the
test file, as ``(endpoint, kind)`` pairs. ``assert_outcomes`` fails for a pair that is not listed and does it, and for a
pair that is listed and no longer does.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded

logger = logging.getLogger("jira-dc-sim-campaign")


AUDIT_LOG = "GET /rest/auditing/1.0/events"

# The (endpoint, kind) pairs whose refusal ends a run as `succeeded` with the change it could not apply left unread (stored
# access kept: nothing lost, nobody gains) and that no open pull request fixes (#6375 and #6212 checked).
# Pairs are named as the cases are: FAIL-002 "<endpoint>", kind; FAIL-003 "<endpoint> after the first page", "502";
# FAIL-004 "<endpoint>", "once"; FAIL-009 "<endpoint> persistent", kind; FAIL-005 "<endpoint>", "throttle".
# Emptied: no run may report success unread.
PARTIAL_SUCCESS: set[tuple[str, str]] = {
    # 403 is the account's lasting answer about a project: notified, and not failed every run (as on Jira Cloud).
    ("POST /rest/api/2/search persistent", "403"), ("POST /rest/api/2/search", "403"),
    # Groups keep their stored members and the project roles theirs.
    *(("GET /rest/api/2/groups/picker", kind) for kind in ("429", "500", "drop", "malformed", "once")),
    # A 200 whose body cannot be read.
    ("GET /rest/api/2/user/list", "malformed"), ("GET /rest/api/2/user/search", "malformed"),
    # The audit log is how an incremental run learns of a deletion: unread, the deleted issue stays until a later run.
    *((AUDIT_LOG, kind) for kind in ("401", "403", "429", "500", "drop", "malformed", "once")),
}

# The (endpoint, kind) pairs a run may answer by taking access away (never a record): the lasting refusals (401, 403) of what
# gives people their access when the account is the one that lacks the privilege (Administer Projects for a project's
# permission scheme and its grants, Administer Jira for the application roles, Browse users and groups for the user list and search, on
# a Jira with no /user/list). The connector then fails closed: it grants the configuring user alone, or links no one, so
# nothing leaks, and the run is recorded as succeeded all the same. The next clean sync must restore it. Found by running the matrix; a pair not listed that does it fails.
FAILS_CLOSED: set[tuple[str, str]] = {
    ("GET /rest/api/2/user/search", "401"), ("GET /rest/api/2/user/search", "403"),
    ("GET /rest/api/2/project/{ref}/permissionscheme", "401"), ("GET /rest/api/2/project/{ref}/permissionscheme", "403"),
    ("GET /rest/api/2/permissionscheme/{scheme_id}/permission", "401"),
    ("GET /rest/api/2/permissionscheme/{scheme_id}/permission", "403"),
    ("GET /rest/api/2/applicationrole", "403"),
}

# What a run that applied nothing of a change may still differ in: a person it did not (yet) give access to.
EXCUSED_WHEN_UNREAD = "DENIED"


class Campaign:
    def __init__(self, w: JiraDcWorld, *, settle: int = 5) -> None:
        self.w = w
        self.settle = settle
        self.n = 0
        self.names = w.usernames()
        self.edited = ""
        self.before: dict[str, set[str]] | None = None
        self.unread = False
        self.partial: set[tuple[str, str]] = set()
        self.gone: set[str] = set()
        self.ran: set[tuple[str, str]] = set()
        self.closed_seen: set[tuple[str, str]] = set()
        self._commented = ""

    def commented_issue(self) -> str:
        """An issue with more comments than the simulator embeds (one, in the tests that read comments), so a run that
        changes it reads its comments through /issue/{id}/comment."""
        if not self._commented:
            for tid, t in sorted(self.w.sim.expected()["ticket_index"].items(), key=lambda kv: int(kv[0])):
                if self.w.sim.vendor_get(f"/rest/api/2/issue/{tid}/comment").json()["total"] >= 2:
                    self._commented = t["key"]
                    break
            assert self._commented, "no issue with two comments in the site"
        return self._commented

    def delete_one(self) -> None:
        """An issue is deleted at the source: its records may (and a full read must) go, so they are not a loss."""
        index = self.w.sim.expected()["ticket_index"]
        for d in self.w.sim.advance(delete=1)["changes"]["delete"]:
            tid = str(d["issue_id"])
            self.gone.add(tid)
            self.gone |= {f"attachment_{f}" for f in index.get(tid, {}).get("files", [])}

    def change(self) -> None:
        """One more person may read GRAN (an Administrators role actor), and an issue changes (a record's name)."""
        user = self.names[self.n % len(self.names)]
        self.n += 1
        key = self.edited = self.commented_issue()
        self.w.mutate({"op": "role_actor", "project": "GRAN", "role": "Administrators", "kind": "user", "who": user},
                      {"op": "issue", "issue": key, "summary": f"Edited {self.n}"})

    async def case(self, endpoint: str, kind: str, rule: dict[str, Any], when: str = "sync", *, full: bool = False) -> int:
        """One faulted run; returns how many times the fault fired. Raises on any safety failure."""
        w = self.w
        label = f"{kind} on {endpoint}"
        if self.before is None:
            self.before = await w.holdings()
        self.change()
        w.faults(rule)
        if when == "fallback":
            w.sim.switches(version="10.2.1")
        outcome = await w.restart() if when == "init" else await w.sync(full=full)
        if when == "fallback":
            w.sim.call("DELETE", "/_sim/jiradc/switches")
        fired = w.clear_faults()  # fails when the fault never fired
        self.ran.add((endpoint, kind))
        # SAFETY 1: nothing lost, in any outcome. A listed fail-closed pair may lose access, never a record.
        after = await w.holdings()
        closed = (endpoint, kind) in FAILS_CLOSED
        lost_all = {who: sorted(self.before[who] - after.get(who, set()) - self.gone) for who in self.before}
        lost_all = {who: v for who, v in lost_all.items() if v}
        lost = {who: v for who, v in lost_all.items() if not (closed and who != "records")}
        assert not lost, f"{label}: a run lost records or access it could not have seen gone: {lost}"
        if closed:
            if lost_all:
                self.closed_seen.add((endpoint, kind))
            assert succeeded(await w.sync()), f"{label}: the next clean sync did not succeed"
            await w.assert_matches_source(f"{label}: after the refusal ended")
            self.before, self.unread = None, False
            return fired
        if succeeded(outcome):
            # SAFETY 2: a success shows no one what Jira does not show them.
            problems = await w.differences()
            # A run that could not read the audit log does not know what was deleted: only those records may still be there.
            undeleted = [p for p in problems if endpoint == AUDIT_LOG and p["type"] == "STALE" and p["record"] in self.gone]
            bad = [p for p in problems if not str(p["type"]).startswith(EXCUSED_WHEN_UNREAD)
                   and p["type"] != "SIM_UNMODELED" and p not in undeleted]
            assert not bad, f"{label}: the run reported success but PipesHub differs from Jira: {bad[:10]}"
            denied = [p for p in problems if str(p["type"]).startswith(EXCUSED_WHEN_UNREAD)
                      and p["type"] not in w.known_gaps()]
            tid = next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == self.edited)
            record = await w.record_for(tid)
            unread_edit = record is None or f"Edited {self.n}" not in str(record["recordName"])
            if denied or unread_edit or undeleted:
                self.partial.add((endpoint, kind))  # a success with part of the change unread (OUTCOME, below)
                self.unread = True
                logger.warning("%s: success with the change unread (%d denied, edit read: %s)", label, len(denied),
                               not unread_edit)
            else:
                self.before, self.unread = None, False
        else:
            # SAFETY 3: what a run that did not succeed gave (it writes what it could read) is what Jira itself gives: no
            # one reads what Jira does not show them, and nothing is stored twice.
            bad = [p for p in await w.differences() if str(p["type"]).startswith(("LEAK", "DUPLICATE"))
                   and p["record"] not in self.gone]
            assert not bad, f"{label}: a run that did not succeed gave access Jira does not give: {bad[:10]}"
            self.unread = True
        return fired

    async def finish(self, label: str) -> None:
        """Jira answers again: the next clean sync reads every change and equals Jira (SAFETY 4)."""
        if self.unread:
            assert succeeded(await self.w.sync()), f"{label}: the next clean sync did not succeed"
            await self.w.assert_matches_source(f"{label}: after Jira answered again")
            self.before, self.unread = None, False

    def assert_outcomes(self, listed: set[tuple[str, str]], what: str) -> None:
        """OUTCOME: the cases that ended as a success with part of the change unread are exactly ``listed``."""
        closed = FAILS_CLOSED & self.ran
        assert self.closed_seen == closed, (
            f"{what}: pairs in FAILS_CLOSED that no longer take access away: {sorted(closed - self.closed_seen)}")
        considered = {pair for pair in listed if pair in self.ran}
        new = sorted(self.partial - considered)
        gone = sorted(considered - self.partial)
        assert not new and not gone, (
            f"{what}: runs that reported success with a change unread and are not in PARTIAL_SUCCESS: {new}; "
            f"listed there but no longer doing it: {gone}")

# pyright: ignore-file

"""A fault campaign over one connector: each case changes the site, refuses one endpoint, runs a sync and checks it.

Two kinds of assertion, kept apart:

SAFETY, never excused: the fault fired; whatever the outcome, no record and no access is lost; a run that did not
succeed gives nobody access Jira does not give them; a run that did succeed leaves no one reading what Jira does not
show them (no LEAK, STALE, DUPLICATE or misfiled record); and once Jira answers again the next clean sync equals Jira
(``finish``).

OUTCOME: a run that could not read what it needed must not report ``succeeded``. Some runs still do (the change they
could not read stays unread, nothing is lost or gained): the pairs that do today are listed in ``PARTIAL_SUCCESS``, as
``(endpoint, kind)``. ``assert_outcomes`` fails for a pair that is not listed and does it, and for a listed pair that no
longer does.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.jira.cloud_sim_world import JiraCloudWorld, succeeded

logger = logging.getLogger("jira-cloud-sim-campaign")

AUDIT_LOG = "GET /rest/api/3/auditing/record"

PEOPLE_LOOKUP = "GET /rest/api/3/user/search"
ISSUE_SEARCH = "POST /rest/api/3/search/jql"

# The (endpoint, kind) pairs whose refusal ends a run as `succeeded` with the change it could not apply left unread (stored
# access kept: nothing lost, nobody gains). Found by running the matrix; see jira_cloud_sim_failure_test.py.
PARTIAL_SUCCESS: set[tuple[str, str]] = {
    # The audit log is the record of deletions. A window that could not be read is not passed: the checkpoint stays and the
    # next run reads it again (``_detect_and_handle_deletions``), so the deleted issues stay readable until then.
    *((AUDIT_LOG, kind) for kind in ("401", "403", "429", "500", "drop", "malformed", "once")),
    # People whose e-mail Jira hides are found one by one by their PipesHub e-mail; a lookup that failed leaves them unlinked
    # until a later run. The listing itself was read.
    *((PEOPLE_LOOKUP, kind) for kind in ("401", "403", "429", "500", "drop", "malformed")),
    # Every project's issue search refused for the whole run: the account is notified of each project and nothing is read,
    # yet the run ends succeeded.
    (f"{ISSUE_SEARCH} persistent", "403"),
}

# The (endpoint, kind) pairs a run may answer by taking access away (never a record): a 403 says the sync account is not an
# administrator of what it asked for, which the connector answers by leaving the account itself alone on it and telling the
# administrator, rather than keep access it can no longer check. The next run that may read it gives the access back.
FAILS_CLOSED: set[tuple[str, str]] = {
    ("GET /rest/api/3/applicationrole", "403"),
    ("GET /rest/api/3/project/{ref}/permissionscheme", "403"),
    ("GET /rest/api/3/permissionscheme/{scheme_id}/permission", "403"),
    # A 404 is taken as "it is not there" (``is_inconclusive_status``): Jira answers a group that is not found with it, so a
    # group's members are read as none.
    ("GET /rest/api/3/applicationrole", "404"),
    ("GET /rest/api/3/group/member", "404"),
    ("GET /rest/api/3/group/member after the first page", "404"),
    # In a full run an issue is read again, and the comments restricted to a role are written from the roles this run read: a
    # comment naming a role no one could tell the id of grants nobody until a run reads the roles (a project's stored roles,
    # which need no id, are kept). A group list the run could not read leaves no role members known either.
    *((f"{path} (full)", kind) for path in ("GET /rest/api/3/group/bulk", "GET /rest/api/3/project/{ref}/role")
      for kind in ("400", "401", "403", "404", "429", "500", "drop", "malformed", "empty-object")),
    # As above, in full runs.
    *((f"{path} (full)", "403") for path in (
        "GET /rest/api/3/applicationrole", "GET /rest/api/3/project/{ref}/permissionscheme",
        "GET /rest/api/3/permissionscheme/{scheme_id}/permission")),
    *((f"{path} (full)", kind) for path, kind in (
        ("GET /rest/api/3/applicationrole", "400"), ("GET /rest/api/3/applicationrole", "404"),
        ("GET /rest/api/3/group/member", "404"))),
}

# What a run that applied nothing of a change may still differ in: a person it did not (yet) give access to.
EXCUSED_WHEN_UNREAD = "DENIED"


class Campaign:
    def __init__(self, w: JiraCloudWorld, *, settle: int = 5) -> None:
        self.w = w
        self.settle = settle
        self.n = 0
        self.names = w.emails()
        self.edited = ""
        self.before: dict[str, set[str]] | None = None
        self.unread = False
        self.partial: set[tuple[str, str]] = set()
        self.gone: set[str] = set()
        self.ran: set[tuple[str, str]] = set()
        self.closed_seen: set[tuple[str, str]] = set()
        self.project = w.projects()[-1]
        self.issue = w.issue_keys(self.project)[0]

    def delete_one(self) -> None:
        """An issue is deleted at the source: its records may (and a full read must) go, so they are not a loss."""
        index = self.w.sim.expected()["ticket_index"]
        for d in self.w.sim.advance(delete=1)["changes"]["delete"]:
            tid = str(d["issue_id"])
            self.gone.add(tid)
            self.gone |= {f"attachment_{f['id']}" for f in index.get(tid, {}).get("files", [])}
            self.gone |= {f"comment_{c['id']}" for c in index.get(tid, {}).get("comments", [])}

    def change(self) -> None:
        """One more person may read a project (a user holder), and an issue changes (a record's name)."""
        user = self.names[self.n % len(self.names)]
        self.n += 1
        self.edited = self.issue
        self.w.mutate({"op": "grant", "project": self.project, "holder": "user", "parameter": user},
                      {"op": "issue", "issue": self.issue, "summary": f"Edited {self.n}"})

    async def case(self, endpoint: str, kind: str, rule: dict[str, Any], when: str = "sync", *, full: bool = False) -> int:
        """One faulted run; returns how many times the fault fired. Raises on any safety failure."""
        w = self.w
        label = f"{kind} on {endpoint}"
        if self.before is None:
            self.before = await w.holdings()
        self.change()
        w.faults(rule)
        outcome = await w.restart() if when == "init" else await w.sync(full=full)
        fired = w.clear_faults()  # fails when the fault never fired
        self.ran.add((endpoint, kind))
        # SAFETY 1: nothing lost, in any outcome. A listed fail-closed pair may lose access, never a record.
        after = await w.holdings()
        lost_all = {who: sorted(self.before[who] - after.get(who, set()) - self.gone) for who in self.before}
        lost_all = {who: v for who, v in lost_all.items() if v}
        assert not lost_all.get("records"), f"{label}: a run lost records it could not have seen gone: {lost_all['records']}"
        if lost_all:
            # Access only: the connector failed closed. Allowed for the pairs of FAILS_CLOSED alone (``assert_outcomes`` names
            # the others); it may not open anything, and the next clean sync must give back what it took.
            self.closed_seen.add((endpoint, kind))
            bad = [p for p in await w.differences() if str(p["type"]).startswith(("LEAK", "DUPLICATE"))
                   and p["record"] not in self.gone]
            assert not bad, f"{label}: a run that took access away gave access Jira does not give: {bad[:10]}"
            assert succeeded(await w.sync()), f"{label}: the next clean sync did not succeed"
            await w.assert_matches_source(f"{label}: after the refusal ended")
            self.before, self.unread = None, False
            return fired
        if succeeded(outcome):
            # SAFETY 2: a success shows no one what Jira does not show them.
            problems = await w.differences()
            undeleted = [p for p in problems if endpoint.startswith(AUDIT_LOG) and p["type"] == "STALE" and p["record"] in self.gone]
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
            # SAFETY 3: what a run that did not succeed gave is what Jira itself gives: no one reads what Jira does not
            # show them, and nothing is stored twice.
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
        """OUTCOME: the cases that ended as a success with part of the change unread are exactly ``listed``, and those that took
        access away exactly the pairs of ``FAILS_CLOSED`` that ran. Both are reported together."""
        closed = FAILS_CLOSED & self.ran
        considered = {pair for pair in listed if pair in self.ran}
        wrong = {
            "take access away and are not in FAILS_CLOSED": self.closed_seen - closed,
            "are in FAILS_CLOSED and no longer take access away": closed - self.closed_seen,
            "report success with a change unread and are not in PARTIAL_SUCCESS": self.partial - considered,
            "are in PARTIAL_SUCCESS and no longer do it": considered - self.partial,
        }
        assert not any(wrong.values()), f"{what}: " + "; ".join(f"{sorted(v)} {k}" for k, v in wrong.items() if v)

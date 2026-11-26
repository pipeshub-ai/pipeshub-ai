# pyright: ignore-file

"""
Jira Data Center on the simulator – a sync that cannot read is not a success, and removes nothing
==================================================================================================

Before each faulted sync the site changes (one more person reads GRAN through a role, an issue is edited). Two kinds of
assertion (sim_campaign.py): SAFETY, never excused (the fault fired; no record or access lost; a run that did not
succeed changed nothing and gave nobody anything; a run that did succeed shows no one what Jira does not; the next clean
sync equals Jira) and OUTCOME (a run that could not read what it needed does not report `succeeded`): the runs that do
report success today are the pairs of ``PARTIAL_SUCCESS``; a pair not listed that does it fails the test, and so does a
listed pair that stops. A change of what such a run reports changes only that set.

A request is refused ``BOUND`` times (a multiple of the attempts the connector makes for one request, measured on the
first endpoint of each kind), so the first request of the endpoint fails for good and the run goes on to what it can read;
one persistent case per kind (the issue search) keeps every request refused.

  TC-JDSIM-FAIL-001  The endpoints the connector calls are exactly the ones this file injects faults into
  TC-JDSIM-FAIL-002  Every endpoint x {401, 403, 429, 500, dropped connection, malformed body}, bounded refusals (one
                     test per kind, one connector shared by its endpoints)
  TC-JDSIM-FAIL-003  A listing that fails after its first page (search, user list, group members); comments are a single
                     page per issue here and are covered by the first-page faults of FAIL-002
  TC-JDSIM-FAIL-004  Every endpoint failing once (503): the sync recovers by retry or is listed in PARTIAL_SUCCESS
  TC-JDSIM-FAIL-005  A throttled search (429 with Retry-After) mid-listing
  TC-JDSIM-FAIL-006  A revoked credential (the sync account deactivated): the sync fails and changes nothing; with the
                     account back, the next sync matches
  TC-JDSIM-FAIL-007  The audit log refused (403, no System Administrator)
  TC-JDSIM-FAIL-008  A content read failing (401, 403, 429, 500, dropped connection) for an attachment download and for the
                     rest of an issue's comments: the stream fails or equals the whole one, never a file or an issue with a
                     part missing. (A cut-short body with a matching length cannot be told from a small file by the client:
                     not asserted.)
  TC-JDSIM-FAIL-009  One persistent case per kind: every issue search refused
"""

import pytest
import requests
from connectors.jira_data_center.sim_campaign import PARTIAL_SUCCESS, Campaign
from connectors.jira_data_center.sim_world import (
    SYNC_ACCOUNT,
    JiraDcWorld,
    drop,
    fault,
    malformed,
    succeeded,
    throttle,
)
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

API = r"^/rest/api/2"
PERSISTENT = 500
BOUND_FACTOR = 3
SEARCH = "POST /rest/api/2/search"
FIRST = "GET /rest/api/2/serverInfo"

# endpoint template (as /_sim/calls names it) -> (path regex, when the connector calls it)
#   fallback: only on a Jira with no /user/list (10.2), where it lists the users; sync: every run; init: when the connector
#   starts (so the test turns it off and on); incremental: a run after the first, which looks for deletions (and confirms
#   each deletion with a read of the issue); stream: when a record's content is read (an issue whose embedded comments are cut
#   short reads the rest of them), not by a sync: TC-JDSIM-FAIL-008.
ENDPOINTS: dict[str, tuple[str, str]] = {
    "GET /rest/api/2/serverInfo": (API + r"/serverInfo$", "sync"),
    "GET /rest/api/2/myself": (API + r"/myself$", "sync"),
    "GET /rest/api/2/user/list": (API + r"/user/list$", "sync"),
    "GET /rest/api/2/user/search": (API + r"/user/search$", "fallback"),
    "GET /rest/api/2/groups/picker": (API + r"/groups/picker$", "sync"),
    "GET /rest/api/2/group/member": (API + r"/group/member$", "sync"),
    "GET /rest/api/2/applicationrole": (API + r"/applicationrole$", "sync"),
    "GET /rest/api/2/project": (API + r"/project$", "sync"),
    "GET /rest/api/2/project/{ref}/permissionscheme": (API + r"/project/[^/]+/permissionscheme$", "sync"),
    "GET /rest/api/2/permissionscheme/{scheme_id}/permission": (API + r"/permissionscheme/\d+/permission$", "sync"),
    "GET /rest/api/2/project/{ref}/role": (API + r"/project/[^/]+/role$", "sync"),
    "GET /rest/api/2/project/{ref}/role/{role_id}": (API + r"/project/[^/]+/role/\d+$", "sync"),
    "GET /rest/api/2/project/{ref}/issuesecuritylevelscheme": (API + r"/project/[^/]+/issuesecuritylevelscheme$", "sync"),
    "GET /rest/api/2/issuesecurityschemes/{scheme_id}/members": (API + r"/issuesecurityschemes/\d+/members$", "sync"),
    "GET /rest/api/2/field": (API + r"/field$", "init"),
    "POST /rest/api/2/search": (API + r"/search$", "sync"),
    "GET /rest/api/2/issue/{ref}": (API + r"/issue/[^/]+$", "incremental"),
    "GET /rest/api/2/issue/{ref}/comment": (API + r"/issue/[^/]+/comment$", "stream"),
    "GET /rest/auditing/1.0/events": (r"^/rest/auditing/1\.0/events$", "incremental"),
    # Read when a record's content is streamed (indexing, a user opening it), not by a sync.
    "GET /secure/attachment/{attachment_id}/{filename:path}": (r"^/secure/attachment/", "stream"),
}

KINDS = {
    "401": lambda rx, **t: fault(rx, 401, **t), "403": lambda rx, **t: fault(rx, 403, **t),
    "429": lambda rx, **t: fault(rx, 429, **t), "500": lambda rx, **t: fault(rx, 500, **t),
    "drop": lambda rx, **t: drop(rx, **t), "malformed": lambda rx, **t: malformed(rx, **t),
}


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimFailure:

    async def test_tc_jdsim_fail_001_the_endpoint_list_is_complete(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"embedded_comments": 1}))
        w.sim.advance(delete=1, issue_edit=2)
        # the audit log is read by the run after the one that saw the change
        w.disable()
        assert succeeded(await w.enable())
        assert succeeded(await w.sync())
        file_record = next(r for r in await w.creator_records() if r["externalRecordId"].startswith("attachment_"))
        w.client.stream_record(file_record["id"]).content  # noqa: B018 - reads the file from Jira
        commented = {r["externalRecordId"]: r for r in await w.creator_records()}
        tid = next(t for t in sorted(w.sim.expected()["ticket_index"], key=int) if t in commented
                   and w.sim.vendor_get(f"/rest/api/2/issue/{t}/comment").json()["total"] >= 2)
        w.client.stream_record(commented[tid]["id"]).content  # noqa: B018 - reads the rest of the comments
        called = set(w.sim.paths_called())
        assert called == set(ENDPOINTS), (
            f"endpoints called but not faulted here: {sorted(called - set(ENDPOINTS))}; "
            f"faulted here but not called: {sorted(set(ENDPOINTS) - called)}")

    @pytest.mark.parametrize("kind", list(KINDS))
    async def test_tc_jdsim_fail_002_every_endpoint_fails(self, jira_dc_world: JiraDcWorld, kind: str) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"embedded_comments": 1}))
        await w.assert_matches_source("after the first sync")
        c = Campaign(w)
        # The first endpoint measures the attempts the connector makes for one request of this kind.
        rx, when = ENDPOINTS[FIRST]
        attempts = await c.case(FIRST, kind, KINDS[kind](rx, max_times=PERSISTENT), when)
        bound = max(1, attempts) * BOUND_FACTOR
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream" or endpoint == FIRST:
                continue
            if when == "incremental":
                c.delete_one()
            await c.case(endpoint, kind, KINDS[kind](rx, max_times=bound), when)
        await c.finish(f"after every endpoint failed with {kind}")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[1] == kind}, f"{kind}")

    @pytest.mark.parametrize("endpoint", ["POST /rest/api/2/search", "GET /rest/api/2/user/list",
                                          "GET /rest/api/2/group/member"])
    async def test_tc_jdsim_fail_003_listings_failing_after_their_first_page(
            self, jira_dc_world: JiraDcWorld, endpoint: str) -> None:
        w = jira_dc_world
        # 130 users: two pages of /user/list and three of a group's members.
        w.sim.reset()
        filler = [{"op": "user_create", "username": f"filler.{i}"} for i in range(len(w.sim.users()), 130)]
        assert succeeded(await w.start(prepare=filler, reset=False))
        rx, when = ENDPOINTS[endpoint]
        c = Campaign(w)
        await c.case(f"{endpoint} after the first page", "502", fault(rx, 502, after_n=1, max_times=PERSISTENT), when,
                     full=True)
        await c.finish(f"after {endpoint} answered again")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[0] == f"{endpoint} after the first page"}, endpoint)

    async def test_tc_jdsim_fail_004_every_endpoint_fails_once(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"embedded_comments": 1}))
        c = Campaign(w)
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream":
                continue
            if when == "incremental":
                c.delete_one()
            await c.case(endpoint, "once", fault(rx, 503, max_times=1), when)
        await c.finish("after every endpoint failed once")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[1] == "once"}, "one 503")

    async def test_tc_jdsim_fail_005_search_throttled_mid_listing(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        c = Campaign(w)
        await c.case(SEARCH, "throttle", throttle(ENDPOINTS[SEARCH][0]), full=True)
        await c.finish("after the throttle lifted")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[1] == "throttle"}, "throttled search")

    @pytest.mark.parametrize("kind", list(KINDS))
    async def test_tc_jdsim_fail_009_search_refused_persistently(self, jira_dc_world: JiraDcWorld, kind: str) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        c = Campaign(w)
        await c.case(f"{SEARCH} persistent", kind, KINDS[kind](ENDPOINTS[SEARCH][0], max_times=PERSISTENT))
        await c.finish(f"after the search answered again ({kind})")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[0] == f"{SEARCH} persistent" and p[1] == kind}, kind)

    async def test_tc_jdsim_fail_006_revoked_credential(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        w.mutate({"op": "user_active", "user": SYNC_ACCOUNT, "active": False})
        outcome = await w.sync()
        assert not succeeded(outcome), "a sync whose every Jira read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after a sync with a deactivated sync account")
        w.mutate({"op": "user_active", "user": SYNC_ACCOUNT, "active": True})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the sync account was active again")

    async def test_tc_jdsim_fail_007_audit_log_refused(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.sim.advance(delete=2)
        w.faults(fault(ENDPOINTS["GET /rest/auditing/1.0/events"][0], 403, max_times=PERSISTENT))
        outcome = await w.sync()
        w.clear_faults()
        if succeeded(outcome):
            # Deletions are unknown to this run: its holdings must still be everything the source held.
            await w.assert_holdings_kept(before, "after a run that could not read the audit log", allow_gains=True)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync, which does not need the audit log")

    @pytest.mark.parametrize("kind", [k for k in KINDS if k != "malformed"])
    @pytest.mark.parametrize("what", ["attachment", "comments"])
    async def test_tc_jdsim_fail_008_content_reads_fail(self, jira_dc_world: JiraDcWorld, what: str, kind: str) -> None:
        """A stream that cannot read the file or the rest of the comments fails or equals the stream before: never a
        file or an issue with a part of it missing."""
        w = jira_dc_world
        assert succeeded(await w.start(switches={"embedded_comments": 1}))
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        index = w.sim.expected()["ticket_index"]
        if what == "attachment":
            fid = next(f for t in sorted(index.values(), key=lambda t: t["key"]) for f in t["files"]
                       if f"attachment_{f}" in by_ext)
            record = by_ext[f"attachment_{fid}"]
            endpoint = "GET /secure/attachment/{attachment_id}/{filename:path}"
        else:
            tid = next(t for t in sorted(index, key=int) if t in by_ext
                       and w.sim.vendor_get(f"/rest/api/2/issue/{t}/comment").json()["total"] >= 2)
            record = by_ext[tid]
            endpoint = "GET /rest/api/2/issue/{ref}/comment"
        want = w.client.stream_record(record["id"]).content
        w.sim.call("POST", "/_sim/calls/reset")
        w.faults(KINDS[kind](ENDPOINTS[endpoint][0], max_times=PERSISTENT))
        try:
            got: bytes | None = w.client.stream_record(record["id"]).content
        except (requests.RequestException, PipeshubClientError):
            got = None
        w.clear_faults()
        assert got is None or got == want, f"{kind}: a failed read of the {what} streamed {len(got or b'')} bytes, not the {len(want)} of the whole"

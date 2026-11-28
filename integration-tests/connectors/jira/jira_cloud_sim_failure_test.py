# pyright: ignore-file

"""
Jira Cloud on the simulator: a sync that cannot read is not a success, and removes nothing
==========================================================================================

Before each faulted sync the site changes (one more person reads a project through a user holder, an issue is edited).
SAFETY (cloud_sim_campaign.py) is never excused: the fault fired; no record or access lost; a run that did not succeed
gave nobody anything; a run that did succeed shows no one what Jira does not; the next clean sync equals Jira. OUTCOME: the
runs that report success with a change unread are the pairs of ``PARTIAL_SUCCESS``; a pair not listed that does it fails,
and so does a listed pair that stops.

  TC-JCSIM-FAIL-001  The endpoints the connector calls are exactly the ones this file injects faults into
  TC-JCSIM-FAIL-002  Every endpoint x {401, 403, 429, 500, dropped connection, malformed body}, bounded refusals
  TC-JCSIM-FAIL-003  A listing that fails after its first page (search, users, group members)
  TC-JCSIM-FAIL-004  Every endpoint failing once (503): the sync recovers by retry or is listed in PARTIAL_SUCCESS
  TC-JCSIM-FAIL-005  A throttled search (429 with Retry-After) mid-listing
  TC-JCSIM-FAIL-006  A revoked credential (the sync account deactivated): the sync fails and changes nothing
  TC-JCSIM-FAIL-007  The audit log refused (403)
  TC-JCSIM-FAIL-008  A content read failing for an attachment and for a restricted comment: the stream fails or equals the
                     whole one
  TC-JCSIM-FAIL-009  One persistent case per kind: every issue search refused
  TC-JCSIM-FAIL-010  A full sync whose issue search answers 400 or 404, first request or later, keeps every record and access
                     (400: "the search request is invalid", 404: not documented for the search; neither says "no issue")
"""

import pytest
import requests
from connectors.jira.cloud_sim_campaign import PARTIAL_SUCCESS, PEOPLE_LOOKUP, Campaign
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    COMMENT_PREFIX,
    FILE_PREFIX,
    SYNC_ACCOUNT,
    JiraCloudWorld,
    drop,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    malformed,
    succeeded,
    throttle,
)
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

API = r"^/rest/api/3"
PERSISTENT = 500
BOUND_FACTOR = 3
SEARCH = "POST /rest/api/3/search/jql"
FIRST = "GET /rest/api/3/myself"

# endpoint template (as /_sim/calls names it) -> (path regex, when the connector calls it)
#   sync: every run; init: when the connector starts (the test turns it off and on); incremental: a run after the first,
#   which looks for deletions; stream: when a record's content is read, not by a sync (TC-JCSIM-FAIL-008).
ENDPOINTS: dict[str, tuple[str, str]] = {
    "GET /rest/api/3/myself": (API + r"/myself$", "sync"),
    "GET /rest/api/3/users/search": (API + r"/users/search$", "sync"),
    "GET /rest/api/3/user/search": (API + r"/user/search$", "sync"),
    "GET /rest/api/3/group/bulk": (API + r"/group/bulk$", "sync"),
    "GET /rest/api/3/group/member": (API + r"/group/member$", "sync"),
    "GET /rest/api/3/applicationrole": (API + r"/applicationrole$", "sync"),
    "GET /rest/api/3/project/search": (API + r"/project/search$", "sync"),
    "GET /rest/api/3/project/{ref}/permissionscheme": (API + r"/project/[^/]+/permissionscheme$", "sync"),
    "GET /rest/api/3/permissionscheme/{scheme_id}/permission": (API + r"/permissionscheme/\d+/permission$", "sync"),
    "GET /rest/api/3/project/{ref}/role": (API + r"/project/[^/]+/role$", "sync"),
    "GET /rest/api/3/project/{ref}/role/{role_id}": (API + r"/project/[^/]+/role/\d+$", "sync"),
    "GET /rest/api/3/project/{ref}/issuesecuritylevelscheme": (API + r"/project/[^/]+/issuesecuritylevelscheme$", "sync"),
    "GET /rest/api/3/issuesecurityschemes/level/member": (API + r"/issuesecurityschemes/level/member$", "sync"),
    "POST /rest/api/3/search/jql": (API + r"/search/jql$", "sync"),
    "POST /rest/api/3/issue/bulkfetch": (API + r"/issue/bulkfetch$", "sync"),
    "GET /rest/api/3/auditing/record": (API + r"/auditing/record$", "incremental"),
    "GET /rest/api/3/issue/{ref}": (API + r"/issue/[^/]+$", "stream"),
    "GET /rest/api/3/issue/{ref}/comment": (API + r"/issue/[^/]+/comment$", "stream"),
    "GET /rest/api/3/issue/{ref}/comment/{comment_id}": (API + r"/issue/[^/]+/comment/\d+$", "stream"),
    "GET /rest/api/3/attachment/content/{attachment_id}": (API + r"/attachment/content/\d+$", "stream"),
}

KINDS = {
    "401": lambda rx, **t: fault(rx, 401, **t), "403": lambda rx, **t: fault(rx, 403, **t),
    "429": lambda rx, **t: fault(rx, 429, **t), "500": lambda rx, **t: fault(rx, 500, **t),
    "drop": lambda rx, **t: drop(rx, **t), "malformed": lambda rx, **t: malformed(rx, **t),
}


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimFailure:

    async def test_tc_jcsim_fail_001_the_endpoint_list_is_complete(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(delete=1, issue_edit=2)
        assert succeeded(await w.sync())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        file_record = next(r for e, r in by_ext.items() if e.startswith(FILE_PREFIX))
        w.client.stream_record(file_record["id"]).content  # noqa: B018 - reads the file from Jira
        ticket = next(r for e, r in sorted(by_ext.items()) if e.isdigit())
        w.client.stream_record(ticket["id"]).content  # noqa: B018
        comment = next((r for e, r in by_ext.items() if e.startswith(COMMENT_PREFIX)), None)
        if comment:
            w.client.stream_record(comment["id"]).content  # noqa: B018
        called = {k for k in w.sim.paths_called() if not k.endswith("unmatched")}
        assert called == set(ENDPOINTS), (
            f"endpoints called but not faulted here: {sorted(called - set(ENDPOINTS))}; "
            f"faulted here but not called: {sorted(set(ENDPOINTS) - called)}")

    @pytest.mark.parametrize("kind", list(KINDS))
    async def test_tc_jcsim_fail_002_every_endpoint_fails(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        c = Campaign(w)
        rx, when = ENDPOINTS[FIRST]
        attempts = await c.case(FIRST, kind, KINDS[kind](rx, max_times=PERSISTENT), when)
        bound = max(1, attempts) * BOUND_FACTOR
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream" or endpoint == FIRST:
                continue
            if when == "incremental":
                c.delete_one()
            # Looked up ten people at a time: refusing only the first few requests would leave which person is missed to chance.
            times = PERSISTENT if endpoint == PEOPLE_LOOKUP else bound
            await c.case(endpoint, kind, KINDS[kind](rx, max_times=times), when)
        await c.finish(f"after every endpoint failed with {kind}")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[1] == kind}, f"{kind}")

    @pytest.mark.parametrize("endpoint", [SEARCH, "GET /rest/api/3/users/search", "GET /rest/api/3/group/member"])
    async def test_tc_jcsim_fail_003_listings_failing_after_their_first_page(
            self, jira_cloud_world: JiraCloudWorld, endpoint: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        filler = [{"op": "group_create", "group": f"filler-{i}"} for i in range(3)]
        assert succeeded(await w.start(prepare=filler, reset=False))
        rx, when = ENDPOINTS[endpoint]
        c = Campaign(w)
        await c.case(f"{endpoint} after the first page", "502", fault(rx, 502, after_n=1, max_times=PERSISTENT), when,
                     full=True)
        await c.finish(f"after {endpoint} answered again")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[0] == f"{endpoint} after the first page"}, endpoint)

    async def test_tc_jcsim_fail_004_every_endpoint_fails_once(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        c = Campaign(w)
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream":
                continue
            if when == "incremental":
                c.delete_one()
            await c.case(endpoint, "once", fault(rx, 503, max_times=1), when)
        await c.finish("after every endpoint failed once")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[1] == "once"}, "one 503")

    async def test_tc_jcsim_fail_005_search_throttled_mid_listing(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        c = Campaign(w)
        await c.case(SEARCH, "throttle", throttle(ENDPOINTS[SEARCH][0]), full=True)
        await c.finish("after the throttle lifted")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[1] == "throttle"}, "throttled search")

    @pytest.mark.parametrize("kind", list(KINDS))
    async def test_tc_jcsim_fail_009_search_refused_persistently(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        c = Campaign(w)
        await c.case(f"{SEARCH} persistent", kind, KINDS[kind](ENDPOINTS[SEARCH][0], max_times=PERSISTENT))
        await c.finish(f"after the search answered again ({kind})")
        c.assert_outcomes({p for p in PARTIAL_SUCCESS if p[0] == f"{SEARCH} persistent" and p[1] == kind}, kind)

    async def test_tc_jcsim_fail_006_revoked_credential(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
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

    async def test_tc_jcsim_fail_007_audit_log_refused(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.sim.advance(delete=2)
        w.faults(fault(ENDPOINTS["GET /rest/api/3/auditing/record"][0], 403, max_times=PERSISTENT))
        outcome = await w.sync()
        w.clear_faults()
        if succeeded(outcome):
            await w.assert_holdings_kept(before, "after a run that could not read the audit log", allow_gains=True)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync, which does not need the audit log")

    @pytest.mark.parametrize("kind", [k for k in KINDS if k != "malformed"])
    @pytest.mark.parametrize("what", ["attachment", "comment"])
    async def test_tc_jcsim_fail_008_content_reads_fail(self, jira_cloud_world: JiraCloudWorld, what: str, kind: str) -> None:
        """A stream that cannot read the file or the comment fails or equals the stream before: never a part missing."""
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        if what == "attachment":
            record = next(r for e, r in sorted(by_ext.items()) if e.startswith(FILE_PREFIX))
            endpoint = "GET /rest/api/3/attachment/content/{attachment_id}"
        else:
            record = next(r for e, r in sorted(by_ext.items()) if e.startswith(COMMENT_PREFIX))
            endpoint = "GET /rest/api/3/issue/{ref}/comment/{comment_id}"
        want = w.client.stream_record(record["id"]).content
        w.sim.call("POST", "/_sim/calls/reset")
        w.faults(KINDS[kind](ENDPOINTS[endpoint][0], max_times=PERSISTENT))
        try:
            got: bytes | None = w.client.stream_record(record["id"]).content
        except (requests.RequestException, PipeshubClientError):
            got = None
        w.clear_faults()
        assert got is None or got == want, f"{kind}: a failed read of the {what} streamed {len(got or b'')} bytes, not the {len(want)} of the whole"

    @pytest.mark.parametrize("after", [0, 1], ids=["first", "later"])
    @pytest.mark.parametrize("status", [400, 404])
    async def test_tc_jcsim_fail_010_refused_search_in_a_full_sync_keeps_the_records(
            self, jira_cloud_world: JiraCloudWorld, status: int, after: int) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.faults(fault(ENDPOINTS[SEARCH][0], status, max_times=PERSISTENT, **({"after_n": after} if after else {})))
        await w.sync(full=True)
        w.clear_faults()
        await w.assert_holdings_kept(before, f"after a full sync whose issue search answered {status}")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after Jira answered again")

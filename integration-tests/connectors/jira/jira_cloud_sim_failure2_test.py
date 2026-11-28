# pyright: ignore-file

"""
Jira Cloud on the simulator: more ways for each endpoint to fail
================================================================

jira_cloud_sim_failure_test.py faults every endpoint with 401, 403, 429 (no Retry-After), 500, a dropped connection and a cut
body, once at the first call and persistently. This file adds the kinds it leaves out, over the same endpoints, with the
same SAFETY checks (cloud_sim_campaign.py: the fault fired, nothing lost, nobody gains, the next clean run equals Jira):

  TC-JCSIM-FAIL2-001  Every endpoint x {404, an empty object}, incremental runs
  TC-JCSIM-FAIL2-002  Group members and the issue search failing after their first page x {404, dropped, empty}, full runs
  TC-JCSIM-FAIL2-003  Every endpoint throttled (429): a token bucket where several requests follow, else one refusal
  TC-JCSIM-FAIL2-004  Every endpoint refused four times (503), more often than it is retried
  TC-JCSIM-FAIL2-005  The content reads (stream) failing: 404, 410, an empty object, a body of the wrong type
  TC-JCSIM-FAIL2-006  Page tokens going wrong on the issue search mid-run: a short page, a repeated token
  TC-JCSIM-FAIL2-007  Every endpoint x {400, 401, 403, 404, 429, 500, dropped, cut short, an empty object} in FULL runs, whose
                      end removes what the run did not see
"""

import pytest
from connectors.jira.cloud_sim_campaign import AUDIT_LOG, ISSUE_SEARCH, PEOPLE_LOOKUP, Campaign
from connectors.jira.cloud_sim_sync import PERSISTENT, SEARCH_RX, end_faults, shaped
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    COMMENT_PREFIX,
    FILE_PREFIX,
    JiraCloudWorld,
    drop,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    malformed,
    page_token,
    succeeded,
    throttle,
)
from connectors.jira.jira_cloud_sim_failure_test import ENDPOINTS, FIRST, KINDS
import requests

from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

KINDS2 = {
    "400": lambda rx, **t: fault(rx, 400, **t),
    "404": lambda rx, **t: fault(rx, 404, **t),
    "410": lambda rx, **t: fault(rx, 410, **t),
    "empty-object": lambda rx, **t: shaped(rx, {"path": "$", "value": {}}, **t),
    "wrong-type": lambda rx, **t: shaped(rx, {"path": "$", "value": "not json at all"}, **t),
}
# Only an incremental run reads changed issues in bulk.
NOT_IN_A_FULL_RUN = {"POST /rest/api/3/issue/bulkfetch"}
SWEPT = ["404", "empty-object"]  # the kinds of the whole-matrix runs (one run per endpoint each); the rest are content reads
# (endpoint, kind) pairs that end as a success with a change unread, found by running this matrix: nothing is lost, nobody
# gains, and the next clean run reads the change (the campaign checks all three). Anything else must not report success.
PARTIAL2: set[tuple[str, str]] = {
    # Deletions of the window unread; the checkpoint stays, the next run removes them.
    *((AUDIT_LOG, kind) for kind in ("404", "empty-object", "4x503")),
    # The project's issues past the first page unread; its stored records stay, the next run reads them.
    (f"{ISSUE_SEARCH} after the first page", "404"),
    # In full runs: a permission scheme that cannot be read keeps the project's stored access, whatever the refusal but 403
    # (which closes it), and the change of access is unread; the run is not told apart from a whole one.
    *((f"{path} (full)", kind) for path in (
        "GET /rest/api/3/permissionscheme/{scheme_id}/permission", "GET /rest/api/3/project/{ref}/permissionscheme")
      for kind in ("400", "401", "404", "429", "500", "drop", "malformed", "empty-object")),
    # Issues of the project unread; its stored records stay.
    *((f"{ISSUE_SEARCH} (full)", kind) for kind in ("400", "403", "404")),
    # People found one by one by e-mail, or a user list cut short or of another shape: those it did not show are not linked yet.
    *((f"{PEOPLE_LOOKUP} (full)", kind) for kind in ("429", "500", "drop", "malformed")),
    *(("GET /rest/api/3/users/search (full)", kind) for kind in ("malformed", "empty-object")),
}


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimFailureMore:

    @pytest.mark.parametrize("kind", SWEPT)
    async def test_tc_jcsim_fail2_001_every_endpoint(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        c = Campaign(w)
        rx, when = ENDPOINTS[FIRST]
        attempts = await c.case(FIRST, kind, KINDS2[kind](rx, max_times=PERSISTENT), when)
        bound = max(1, attempts) * 3
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream" or endpoint == FIRST:
                continue
            if when == "incremental":
                c.delete_one()
            await c.case(endpoint, kind, KINDS2[kind](rx, max_times=bound), when)
        await c.finish(f"after every endpoint failed with {kind}")
        c.assert_outcomes({p for p in PARTIAL2 if p[1] == kind}, kind)

    @pytest.mark.parametrize("kind", ["404", "drop", "empty"])
    @pytest.mark.parametrize("endpoint", ["GET /rest/api/3/group/member", "POST /rest/api/3/search/jql"])
    async def test_tc_jcsim_fail2_002_after_the_first_page(self, jira_cloud_world: JiraCloudWorld, endpoint: str, kind: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        assert succeeded(await w.start(prepare=[{"op": "group_create", "group": f"filler-{i}"} for i in range(60)], reset=False))
        rx, when = ENDPOINTS[endpoint]
        trig = {"after_n": 1, "max_times": PERSISTENT}
        rule = {"404": lambda: fault(rx, 404, **trig), "429": lambda: fault(rx, 429, **trig), "503": lambda: fault(rx, 503, **trig),
                "drop": lambda: drop(rx, **trig), "cut": lambda: malformed(rx, **trig),
                "empty": lambda: shaped(rx, {"path": "$", "value": {}}, **trig)}[kind]()
        c = Campaign(w)
        await c.case(f"{endpoint} after the first page", kind, rule, when, full=True)
        await c.finish(f"after {endpoint} answered again")
        c.assert_outcomes({p for p in PARTIAL2 if p == (f"{endpoint} after the first page", kind)}, endpoint)

    async def test_tc_jcsim_fail2_003_throttle_everywhere(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.call("POST", "/_sim/calls/reset")
        assert succeeded(await w.sync())
        counts = w.sim.paths_called()
        c = Campaign(w)
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream":
                continue
            if when == "incremental":
                c.delete_one()
            # The bucket's first token answers the only request of an endpoint called once a run: that one is refused outright.
            rule = throttle(rx) if counts.get(endpoint, 0) > 1 else fault(rx, 429, max_times=1)
            await c.case(endpoint, "throttle", rule, when)
        await c.finish("after every endpoint was throttled")
        # Which requests the bucket refuses depends on timing, so only safety is asserted: the campaign's, and that being
        # throttled never takes access away.
        assert not c.closed_seen, f"throttling took access away: {sorted(c.closed_seen)}"

    @pytest.mark.parametrize("times", [4])
    async def test_tc_jcsim_fail2_004_refused_a_few_times(self, jira_cloud_world: JiraCloudWorld, times: int) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        c = Campaign(w)
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream":
                continue
            if when == "incremental":
                c.delete_one()
            await c.case(endpoint, f"{times}x503", fault(rx, 503, max_times=times), when)
        await c.finish(f"after every endpoint refused {times} times")
        c.assert_outcomes({p for p in PARTIAL2 if p[1] == f"{times}x503"}, f"{times} refusals")

    @pytest.mark.parametrize("kind", ["400", "401", "403", "404", "429", "500", "drop", "malformed", "empty-object"])
    async def test_tc_jcsim_fail2_007_every_endpoint_in_a_full_run(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        """A run that ends by removing what it did not see: nothing the sync account still sees may go."""
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.call("POST", "/_sim/calls/reset")
        assert succeeded(await w.sync(full=True))
        called = set(w.sim.paths_called())
        c = Campaign(w)
        make = {**KINDS, **KINDS2}[kind]
        for endpoint, (rx, when) in ENDPOINTS.items():
            if when == "stream" or endpoint == FIRST or endpoint in NOT_IN_A_FULL_RUN:
                assert endpoint not in called or endpoint not in NOT_IN_A_FULL_RUN, f"{endpoint} is called by a full run"
                continue
            if when == "incremental":
                c.delete_one()  # a full run looks for deletions too
            await c.case(f"{endpoint} (full)", kind, make(rx, max_times=PERSISTENT), when, full=True)
        await c.finish(f"after every endpoint failed with {kind} in full runs")
        c.assert_outcomes({p for p in PARTIAL2 if p[1] == kind and p[0].endswith("(full)")}, f"{kind}, full runs")

    @pytest.mark.parametrize("kind", ["404", "410", "empty-object", "wrong-type"])
    @pytest.mark.parametrize("what", ["attachment", "comment", "issue"])
    async def test_tc_jcsim_fail2_005_content_reads(self, jira_cloud_world: JiraCloudWorld, what: str, kind: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        if what == "attachment":
            record = next(r for e, r in sorted(by_ext.items()) if e.startswith(FILE_PREFIX))
            endpoint = "GET /rest/api/3/attachment/content/{attachment_id}"
        elif what == "comment":
            record = next(r for e, r in sorted(by_ext.items()) if e.startswith(COMMENT_PREFIX))
            endpoint = "GET /rest/api/3/issue/{ref}/comment/{comment_id}"
        else:
            record = next(r for e, r in sorted(by_ext.items()) if e.isdigit())
            endpoint = "GET /rest/api/3/issue/{ref}"
        want = w.client.stream_record(record["id"]).content
        w.sim.call("POST", "/_sim/calls/reset")
        w.faults(KINDS2[kind](ENDPOINTS[endpoint][0], max_times=PERSISTENT))
        try:
            got: bytes | None = w.client.stream_record(record["id"]).content
        except (requests.RequestException, PipeshubClientError):
            got = None
        end_faults(w)
        assert got is None or got == want, f"{kind}: a failed read of the {what} streamed {len(got or b'')} bytes, not the {len(want)} of the whole"

    @pytest.mark.parametrize("mode", ["short", "repeat"])
    async def test_tc_jcsim_fail2_006_tokens_mid_run(self, jira_cloud_world: JiraCloudWorld, mode: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.call("POST", "/_sim/calls/reset")
        assert succeeded(await w.sync())
        searches = w.sim.paths_called().get("POST /rest/api/3/search/jql", 0)
        c = Campaign(w)
        await c.case(f"{SEARCH_RX} mid-run", mode, page_token(SEARCH_RX, mode, after_n=max(1, searches // 2)), full=True)
        await c.finish(f"after the search tokens were right again ({mode})")

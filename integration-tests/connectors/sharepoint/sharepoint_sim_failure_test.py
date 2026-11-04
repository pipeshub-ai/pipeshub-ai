# pyright: ignore-file

"""
SharePoint Online on the simulator – syncs that cannot read everything
======================================================================

The simulator fails one kind of read while the connector syncs. What PipesHub knew must survive (nobody
loses access because a read failed), and a run that skipped part of the source must not be recorded as a
success. The finding each test proves is in brackets; those fail on upstream main and pass with the
audit's fixes.

  TC-SPSIM-FAIL-001  A full sync that cannot list one site's libraries, read SharePoint REST, or read one
                     library's delta removes nothing and is not a success
                     [F-FULLSYNC-PARTIAL-REMOVES, F-SUCCESS-ON-FAILURE]
  TC-SPSIM-FAIL-002  A failed read of the sites' permissions keeps everyone's access [F-SITE-ACL-FAILURE]
  TC-SPSIM-FAIL-003  A failed read of a group's members keeps the members [F-MEMBER-FAILURE-WIPES]
  TC-SPSIM-FAIL-004  SharePoint REST throttling is retried and costs nobody access [F-SPREST-NO-RETRY]
  TC-SPSIM-FAIL-005  An app that cannot read sites fails the connection test, so the connector is not
                     enabled to report empty syncs as successes [F-CONNECTION-TEST]
  TC-SPSIM-FAIL-006  An app without Graph Sites.FullControl.All fails the connection test: without it delta
                     never reports user grants changed on items with their own permissions
"""

import logging

import pytest
from connectors.sharepoint.sim_tenants import base_tenant
from connectors.sharepoint.sim_world import (
    QUIET,
    SharePointWorld,
    fault,
    library_regex,
    succeeded,
)
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

logger = logging.getLogger("sharepoint-sim-failure-test")


@pytest.mark.integration
@pytest.mark.sharepoint_sim
@pytest.mark.asyncio(loop_scope="session")
class TestSharePointSimFailures:

    @pytest.mark.parametrize("target", ["drives", "rest", "delta"],
                             ids=["drives-listing", "sharepoint-rest", "delta"])
    async def test_tc_spsim_fail_001_full_sync_with_failures_removes_nothing(
        self, sharepoint_world: SharePointWorld, target: str,
    ) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.faults(fault(library_regex(w, "eng", "eng-docs", target), 503))
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not succeeded(outcome), (
            f"a full sync that could not read part of the source ({target}) was recorded as {outcome}"
        )
        await w.assert_matches_source(f"after a full sync that could not read the {target} of Engineering")

    async def test_tc_spsim_fail_002_site_permission_read_failure_keeps_access(
        self, sharepoint_world: SharePointWorld,
    ) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        # Alternatives: one read or the other, depending on how the code under test reads site permissions.
        w.faults(fault(r"/_api/web/associated", 503), fault(r"/_api/web/roleassignments", 503))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), f"a sync that could not read site permissions was recorded as {outcome}"
        await w.assert_matches_source("after a sync whose site permission reads failed")

    async def test_tc_spsim_fail_003_group_member_read_failure_keeps_members(
        self, sharepoint_world: SharePointWorld,
    ) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        # A changed group is what makes an incremental sync read members again.
        w.ops({"op": "rename_group", "group": "fin", "display_name": "Finance team"})
        # Upstream reads /members, the fix /transitiveMembers: both must fail for the test to mean anything.
        w.faults(fault(r"/groups/[^/]+/(members|transitiveMembers)", 503))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), f"a sync that could not read group members was recorded as {outcome}"
        await w.assert_matches_source("after a sync whose group member reads failed")

    async def test_tc_spsim_fail_004_sharepoint_rest_throttling(self, sharepoint_world: SharePointWorld) -> None:
        """A site grant only SharePoint REST reports (role assignments) must arrive through the throttling:
        a throttled read that was dropped and left the old access would deny frank the Finance files."""
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "grant", "target": "fin", "kind": "user", "ref": "frank", "roles": ["Read"]})
        w.faults({"match": {"path_regex": "/_api/"},
                  "effect": {"type": "rate_limit", "capacity": 3, "refill_per_s": 3}})
        outcome = await w.sync()
        w.clear_faults()
        assert succeeded(outcome), f"a sync throttled by SharePoint REST did not finish: {outcome}"
        await w.assert_matches_source("after a sync throttled by SharePoint REST, with frank granted on Finance")

    async def test_tc_spsim_fail_005_connection_test_refuses_app_without_site_access(
        self, sharepoint_world: SharePointWorld,
    ) -> None:
        w = sharepoint_world
        tenant = base_tenant(app={"graph_roles": ["User.Read.All", "Group.Read.All", "GroupMember.Read.All",
                                                  "Directory.Read.All"]})
        await w.start(tenant, behaviours=QUIET, enable=False)
        with pytest.raises(PipeshubClientError):
            w.client.toggle_sync(w.connector_id, enable=True)
        assert not w.client.get_connector(w.connector_id).get("isActive"), (
            "the connector was enabled although its app cannot read any site"
        )

    async def test_tc_spsim_fail_006_connection_test_refuses_app_without_full_control(
        self, sharepoint_world: SharePointWorld,
    ) -> None:
        w = sharepoint_world
        tenant = base_tenant(app={"graph_roles": ["Sites.Read.All", "Files.Read.All", "User.Read.All",
                                                  "Group.Read.All", "GroupMember.Read.All", "Directory.Read.All"]})
        await w.start(tenant, behaviours=QUIET, enable=False)
        # The refusal names the permission, so the admin knows what to grant.
        with pytest.raises(PipeshubClientError, match="Sites.FullControl.All"):
            w.client.toggle_sync(w.connector_id, enable=True)
        assert not w.client.get_connector(w.connector_id).get("isActive"), (
            "the connector was enabled although its app lacks Graph Sites.FullControl.All"
        )

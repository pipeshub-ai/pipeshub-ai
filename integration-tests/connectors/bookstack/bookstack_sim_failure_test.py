# pyright: ignore-file

"""
BookStack on the simulator – failures lose nothing, remove nothing, and are not reported as success
===================================================================================================

  TC-BSSIM-FAIL-001  A page whose permissions cannot be read keeps its access; the sync is not a success
                     [F-ERROR-AS-PERMISSIONS]
  TC-BSSIM-FAIL-002  A full sync whose page listing fails removes nothing and is not a success
                     [F-FULLSYNC-PARTIAL]
  TC-BSSIM-FAIL-003  A revoked token fails the sync and the admin is told [F-NO-NOTIFICATIONS]
  TC-BSSIM-FAIL-004  A change made during an audit-log outage arrives once it answers [F-SUCCESS-ON-FAILURE]
"""

import pytest
from connectors.bookstack.sim_instances import plain_instance
from connectors.bookstack.sim_world import TOKEN_ID, BookStackWorld, fault, succeeded


@pytest.mark.integration
@pytest.mark.bookstack_sim
@pytest.mark.asyncio(loop_scope="session")
class TestBookStackSimFailure:

    async def test_tc_bssim_fail_001_permission_read_failure(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "update_page", "id": "welcome", "data": {"markdown": "Edited."}})
        w.faults(fault(rf"^/api/content-permissions/page/{w.key('welcome')}$", 500))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), "a sync that could not read a page's permissions reported success"
        await w.assert_matches_source("after the permission read failed")

    async def test_tc_bssim_fail_002_full_sync_listing_failure(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.faults(fault(r"^/api/pages$", 503, max_times=200))
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not succeeded(outcome), "a full sync that could not list pages reported success"
        await w.assert_matches_source("after a full sync without a page listing")

    async def test_tc_bssim_fail_003_revoked_token(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "revoke_token", "token_id": TOKEN_ID})
        assert not succeeded(await w.sync()), "a sync with a revoked token reported success"
        # The notice itself is checked by the harness scenario revoked_token_is_reported_once; this suite has
        # no notification reader.

    async def test_tc_bssim_fail_004_audit_log_outage(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "create_page", "key": "late", "data": {"book_id": "@handbook", "name": "Late"}},
              {"op": "update_user", "id": "carol", "data": {"roles": ["@role:finance"]}})
        w.faults(fault(r"^/api/audit-log$", 502, max_times=200))
        assert not succeeded(await w.sync()), "a sync that could not read the audit log reported success"
        w.clear_faults()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the audit log answered again")

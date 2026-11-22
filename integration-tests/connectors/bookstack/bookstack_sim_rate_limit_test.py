# pyright: ignore-file

"""
BookStack on the simulator – a 429 is waited out, and a 429 that does not stop loses nothing
=============================================================================================

BookStack answers a request over its limit with 429 and Retry-After (Laravel's throttle); the simulator's injected
429s carry the same headers. TC-BSSIM-SYNC-007 covers the instance's own limit; these inject the 429s.

  TC-BSSIM-RL-001  A first sync refused with 429 every few requests waits each one out and matches BookStack
                   [F-RATE-LIMIT]
  TC-BSSIM-RL-002  An incremental sync whose audit-log and permission reads are refused once each still brings
                   every change [F-RATE-LIMIT]
  TC-BSSIM-RL-003  A full sync whose page listing BookStack keeps refusing removes nothing and is not a success;
                   the next sync matches [F-FULLSYNC-PARTIAL]
  TC-BSSIM-RL-004  Changes made while the audit log keeps answering 429 arrive once it answers; the refused sync
                   is not a success [F-SUCCESS-ON-FAILURE]
"""

import pytest
from connectors.bookstack.sim_instances import plain_instance
from connectors.bookstack.sim_world import BookStackWorld, fault, succeeded

# More 429s than the connector's retries for every request the run makes, so BookStack never stops refusing.
KEEPS_REFUSING = 200


@pytest.mark.integration
@pytest.mark.bookstack_sim
@pytest.mark.asyncio(loop_scope="session")
class TestBookStackSimRateLimit:

    async def test_tc_bssim_rl_001_first_sync_throttled_now_and_then(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        await w.start(plain_instance(), enable=False)
        w.faults(fault(r"^/api/", 429, every_nth=5))
        outcome = await w.enable()
        w.clear_faults()
        assert succeeded(outcome), f"a first sync that was only throttled did not succeed: {outcome}"
        await w.assert_matches_source("after a first sync refused with 429 every fifth request")

    async def test_tc_bssim_rl_002_incremental_reads_throttled_once(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "create_page", "key": "new", "data": {"book_id": "@handbook", "name": "New starter"}},
              {"op": "update_page", "id": "welcome", "data": {"markdown": "Second edition."}},
              {"op": "update_user", "id": "carol", "data": {"roles": ["@role:finance"]}})
        w.faults(fault(r"^/api/audit-log$", 429, max_times=1),
                 fault(r"^/api/content-permissions/", 429, max_times=2))
        outcome = await w.sync()
        w.clear_faults()
        assert succeeded(outcome), f"an incremental sync that was only throttled did not succeed: {outcome}"
        await w.assert_matches_source("after an incremental sync throttled on the audit log and permissions")

    async def test_tc_bssim_rl_003_full_sync_listing_keeps_refusing(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        before = {r["externalRecordId"] for r in await w.records()}
        w.faults(fault(r"^/api/pages$", 429, max_times=KEEPS_REFUSING))
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not succeeded(outcome), "a full sync whose page listing kept answering 429 reported success"
        after = {r["externalRecordId"] for r in await w.records()}
        assert before <= after, f"a full sync without a page listing removed records: {sorted(before - after)}"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the page listing answered again")

    async def test_tc_bssim_rl_004_audit_log_keeps_refusing(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "create_page", "key": "late", "data": {"book_id": "@handbook", "name": "Late"}},
              {"op": "delete", "id": "expenses"},
              {"op": "update_user", "id": "carol", "data": {"roles": ["@role:finance"]}})
        w.faults(fault(r"^/api/audit-log$", 429, max_times=KEEPS_REFUSING))
        assert not succeeded(await w.sync()), "a sync whose audit log kept answering 429 reported success"
        w.clear_faults()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the audit log stopped answering 429")

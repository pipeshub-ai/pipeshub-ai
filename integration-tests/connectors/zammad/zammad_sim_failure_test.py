# pyright: ignore-file

"""
Zammad on the simulator – a sync that cannot read is not a success, and removes nothing
=======================================================================================

A sync whose reads were refused or failed must end with lastSyncOutcome "failed", never "succeeded", and must leave
behind what it could not confirm: every record stays and nobody gains or loses access. Once Zammad answers again, the
next sync must match it. Every injected fault must fire, or the test proves nothing.

  TC-ZMSIM-FAIL-001  A revoked token: the sync fails and changes nothing [F-ZAMMAD-REVOKED-TOKEN]
  TC-ZMSIM-FAIL-002  6.4.1: the token's owner loses the Admin role, so GET /groups is refused (403): the sync fails
                     and changes nothing; with the role back, the next sync matches [F-ZAMMAD-64-AGENT-TOKEN]
  TC-ZMSIM-FAIL-003  6.4.1: an agent's own token cannot list groups: PipesHub either refuses to turn the connector on
                     or the run fails; nothing is synced [F-ZAMMAD-64-AGENT-TOKEN]
  TC-ZMSIM-FAIL-004  The ticket search keeps answering 503: the sync fails and keeps the ticket deleted meanwhile;
                     once it answers, the next sync brings every change [F-ZAMMAD-SEARCH-FAILURE]
  TC-ZMSIM-FAIL-005  A full sync whose search fails after the first page (5xx mid-listing) fails and removes nothing;
                     the next sync matches
  TC-ZMSIM-FAIL-006  A search throttled with 429 and Retry-After mid-listing: a sync that reports success holds every
                     change; one that does not removes nothing; the next sync matches
"""

import pytest
from connectors.zammad.sim_instances import AGENT_TOKEN, AGENT_TOKENS, TOKEN, TOKENS, plain_instance
from connectors.zammad.sim_world import REFUSED, ZammadWorld, fault, succeeded

SEARCH = r"^/api/v1/tickets/search$"
# More failures than the connector's retries for every request a run makes, so Zammad never stops refusing.
KEEPS_FAILING = 500


@pytest.mark.integration
@pytest.mark.zammad_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZammadSimFailure:

    async def test_tc_zmsim_fail_001_revoked_token(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance()))
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        w.ops({"op": "revoke_token", "token": TOKEN})
        outcome = await w.sync()
        assert not succeeded(outcome), "a sync whose every Zammad read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after a sync with a revoked token")

    async def test_tc_zmsim_fail_002_token_owner_loses_admin_on_6_4(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance(), behaviours={"version": "6.4"}))
        await w.assert_matches_source("6.4: after the first sync")
        before = await w.holdings()
        w.ops({"op": "set_user_roles", "user": "admin", "roles": ["Agent"]})
        outcome = await w.sync()
        assert not succeeded(outcome), "6.4: a sync that could not list the groups (403) reported success"
        await w.assert_holdings_kept(before, "6.4: after a sync that could not list the groups")
        w.ops({"op": "set_user_roles", "user": "admin", "roles": ["Admin", "Agent"]})
        assert succeeded(await w.sync())
        await w.assert_matches_source("6.4: after the token's owner was an admin again")

    async def test_tc_zmsim_fail_003_agent_token_on_6_4(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        doc = plain_instance(tokens=TOKENS + AGENT_TOKENS)
        outcome = await w.start(doc, behaviours={"version": "6.4"}, token=AGENT_TOKEN)
        assert outcome == REFUSED or not succeeded(outcome), (
            f"6.4: a token that cannot list groups was turned on and its sync reported success ({outcome})")
        assert not await w.records(), "6.4: a token that cannot list groups synced records"

    async def test_tc_zmsim_fail_004_search_keeps_failing(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance()))
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        w.ops({"op": "delete_ticket", "ticket": "printer"},
              {"op": "update_ticket", "ticket": "laptop", "data": {"title": "Laptop will not boot (escalated)"}},
              {"op": "create_ticket", "key": "late", "title": "Filed during the outage", "group": "billing",
               "customer": "carol"})
        w.faults(fault(SEARCH, 503, max_times=KEEPS_FAILING))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), "a sync whose ticket search kept failing (503) reported success"
        await w.assert_holdings_kept(before, "after a sync whose ticket search kept failing")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the ticket search answered again")

    async def test_tc_zmsim_fail_005_full_sync_fails_mid_listing(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance()))
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        w.faults(fault(SEARCH, 502, after_n=1, max_times=KEEPS_FAILING))
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not succeeded(outcome), "a full sync whose ticket search failed after the first page reported success"
        await w.assert_holdings_kept(before, "after a full sync that failed mid-listing")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the search answered again")

    async def test_tc_zmsim_fail_006_throttled_mid_listing(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance()))
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        w.ops({"op": "create_ticket", "key": "late", "title": "Filed while throttled", "group": "support",
               "customer": "carol"},
              {"op": "update_ticket", "ticket": "invoice", "data": {"title": "Invoice 4471 (credited)"}})
        # One search every two seconds; a refused one answers 429 with Retry-After: 2.
        w.faults({"match": {"path_regex": SEARCH}, "effect": {"type": "rate_limit", "capacity": 1, "refill_per_s": 0.5}})
        outcome = await w.sync(full=True)
        w.clear_faults()
        if succeeded(outcome):
            await w.assert_matches_source("after a throttled sync that reported success")
            return
        # It may have written the part of the change it did read; it must not have removed anything.
        await w.assert_holdings_kept(before, "after a throttled sync that did not succeed", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the throttle lifted")

# pyright: ignore-file

"""
Zammad on the simulator – what reaches PipesHub, and when
==========================================================

The incremental cases run on both answers the simulator gives for a Zammad version: 6.5, which counts a search's
tickets (so the connector can tell a group whose tickets went), and 6.4.1, the version the live suite's stack runs,
which cannot. The stack lists every group in full on every sync (ZAMMAD_FULL_TICKET_LISTING_INTERVAL_MS=0, the
compose default), so on 6.4 a deleted ticket is also gone after the next sync.

  TC-ZMSIM-SYNC-001        First sync matches Zammad; a sync with nothing changed creates, removes and re-versions
                           nothing
  TC-ZMSIM-SYNC-002[6.x]   A new ticket, a retitled one (same record, higher version), a new article with an
                           attachment, a ticket moved to another group and a deleted ticket arrive on the next sync
                           [F-ZAMMAD-DELETE-LAG]
  TC-ZMSIM-SYNC-003        Merged tickets: both keep their records; the target holds the moved articles' attachments
  TC-ZMSIM-SYNC-004        A ticket whose e-mail article has a null ``from`` is synced [F-ZAMMAD-NULL-FROM]
  TC-ZMSIM-SYNC-005        A group filter: only the kept group syncs; after the filter changes, the run that turns the
                           connector back on is a full one, so the excluded group's tickets leave and the newly kept
                           group's arrive [F-ZAMMAD-FILTER-CHANGE]
  TC-ZMSIM-SYNC-006        On 6.5 an agent's own token syncs the tickets that agent may read
"""

import pytest
from connectors.zammad.sim_instances import AGENT_TOKEN, AGENT_TOKENS, TOKENS, plain_instance
from connectors.zammad.sim_world import ZammadWorld, group_filter, succeeded

VERSIONS = [pytest.param({"version": "6.5"}, id="6.5"), pytest.param({"version": "6.4"}, id="6.4")]


@pytest.mark.integration
@pytest.mark.zammad_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZammadSimSync:

    async def test_tc_zmsim_sync_001_first_and_unchanged_sync(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance()))
        await w.assert_matches_source("after the first sync")
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert succeeded(await w.sync())
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert after == before, f"a sync with nothing changed in Zammad changed records: {before} -> {after}"
        await w.assert_matches_source("after a sync with nothing changed")

    @pytest.mark.parametrize("version", VERSIONS)
    async def test_tc_zmsim_sync_002_incremental_changes(self, zammad_world: ZammadWorld, version: dict) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance(), behaviours=version))
        laptop = str(w.key("ticket", "laptop"))
        before = await w.record_for(laptop)
        w.ops({"op": "create_ticket", "key": "monitor", "title": "Monitor flickers", "group": "support",
               "customer": "carol", "articles": [{"body": "The second monitor flickers after waking."}]},
              {"op": "update_ticket", "ticket": "laptop", "data": {"title": "Laptop will not boot (escalated)"}},
              {"op": "add_article", "ticket": "printer", "body": "Technician on the way.", "sender": "Agent",
               "attachments": [{"filename": "visit.txt", "content": "Tuesday at ten"}]},
              {"op": "move_ticket", "ticket": "invoice", "group": "support"},
              {"op": "create_ticket", "key": "doomed", "title": "Filed by mistake", "group": "billing",
               "customer": "carol"})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"{version['version']}: after new, retitled, moved tickets and a new article")
        after = await w.record_for(laptop)
        assert before and after and after["id"] == before["id"], f"the retitled ticket got a new record: {before} -> {after}"
        assert (after["version"] or 0) > (before["version"] or 0), f"the retitled ticket kept its version: {after}"
        w.ops({"op": "delete_ticket", "ticket": "doomed"}, {"op": "delete_ticket", "ticket": "printer"})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"{version['version']}: after two tickets were deleted")

    async def test_tc_zmsim_sync_003_merged_tickets(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        doc = plain_instance()
        doc["tickets"][1]["articles"] = [{"body": "Paper jams on duplex.", "sender": "Customer",
                                          "attachments": [{"filename": "jam.txt", "content": "tray 2"}]}]
        assert succeeded(await w.start(doc))
        w.ops({"op": "merge_tickets", "ticket": "printer", "into": "laptop"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the printer ticket was merged into the laptop ticket")

    async def test_tc_zmsim_sync_004_article_without_sender_address(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        doc = plain_instance()
        doc["tickets"].append({"key": "mail", "title": "Mailed in", "group": "support", "customer": "carol",
                               "articles": [{"body": "Forwarded text.", "type": "email", "sender": "Customer",
                                             "from": None}]})
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync, with an e-mail article whose from is null")
        w.ops({"op": "add_article", "ticket": "laptop", "body": "Forwarded again.", "type": "email",
               "sender": "Customer", "from": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after another e-mail article whose from is null")

    async def test_tc_zmsim_sync_005_group_filter_change(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        doc = plain_instance()
        await w.start(doc, enable=False)
        support, billing = w.key("group", "support"), w.key("group", "billing")
        w.scope.groups_in = {support}
        w.client.update_connector_filters_sync_config(w.connector_id, filters=group_filter("in", [support]))
        assert succeeded(await w.enable())
        await w.assert_matches_source("with only the Support group in the filter")
        # Billing's tickets are older than the last sync: only a full sync finds them.
        assert succeeded(await w.restart(filters=group_filter("not_in", [support])))
        await w.assert_matches_source(f"after the filter changed to exclude Support (group {support}), keeping "
                                      f"Billing (group {billing})")

    async def test_tc_zmsim_sync_006_agent_token_on_6_5(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        doc = plain_instance(tokens=TOKENS + AGENT_TOKENS)
        assert succeeded(await w.start(doc, behaviours={"version": "6.5"}, token=AGENT_TOKEN))
        await w.assert_matches_source("after a first sync with Ann's own token")

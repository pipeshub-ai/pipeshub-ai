# pyright: ignore-file

"""
Zendesk on the simulator: what a sync holds
===========================================

The connector reads the account the simulator holds (tickets, their public comments' files, Help Center articles and
their files) and every person must read exactly what Zendesk lets them read. The letter in brackets is the area a case
covers: [A] sign-in, [B] first sync, [C] unchanged sync, [D] changes, [E] access, [F] deletion and full sync, [G] filters,
[H] paging and races, [I] failures, [J] content, [K] reindex, [L] lifecycle, [M] vendor variants, [N] security, [O] guard.

  TC-ZDSIM-SYNC-001  The desk account (every access rule once): every record, its parent and who may read it equal the
                     simulator's oracle [B, E]
  TC-ZDSIM-SYNC-002  The same account with custom roles (all / within groups / plus public groups / assigned only /
                     within organization) [B, E]
  TC-ZDSIM-SYNC-003  A plan without custom roles answers 403: the agents' own restrictions still apply [M]
  TC-ZDSIM-SYNC-004  Custom roles answering 404 [M]
  TC-ZDSIM-SYNC-005  An account with no Help Center: tickets still sync [M]
  TC-ZDSIM-SYNC-006  A second sync with nothing changed creates, removes and re-versions nothing [C]
  TC-ZDSIM-SYNC-007  The platform's full sync rebuilds the same records with the same ids, no duplicates [F]
  TC-ZDSIM-SYNC-008  The seeded ticket and article of the live suite: name, web link, timestamps, tags, status, group,
                     its grant, the assignee's identity, the article's title and section [B] (live 04-15)
  TC-ZDSIM-SYNC-009  The ticket stream holds the public comments and never an internal note; the article stream holds
                     the live body [J] (live 16-17)
  TC-ZDSIM-SYNC-010  The seeded ticket reaches completed indexing [K] (live 18)
  TC-ZDSIM-SYNC-011  The live suite's connector: one group chosen in the group filter holds that group's tickets only [G]
  TC-ZDSIM-SYNC-012  Pages of two everywhere (lists and exports), then a second sync of changed tickets [H]
  TC-ZDSIM-SYNC-013  Every list ignores the cursor parameters and answers offset-style [H, M]
  TC-ZDSIM-SYNC-014  Zendesk holding the most recent minute of the export back: strict xfail, a simulator defect (an empty export) [M]
  TC-ZDSIM-SYNC-015  A section list that leaves out nested sections [M]
  TC-ZDSIM-SYNC-016  Several thousand-style bulk: many tickets, users and articles across pages [H]
"""

import pytest
from connectors.zendesk.sim_instances import bulk, desk, it_seed
from connectors.zendesk.sim_world import ZendeskWorld, group_filter, succeeded

from helper.indexing_wait import wait_until_record_indexing_completed


def _ms(value: str) -> int:
    from datetime import datetime  # noqa: PLC0415
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _source(w: ZendeskWorld, path: str) -> dict:
    """What Zendesk answers the account's own token, asked through the simulator's published port."""
    import requests  # noqa: PLC0415
    resp = requests.get(f"{w.sim.base_url}{path}", timeout=30, headers={
        "Authorization": f"Bearer {w.token_for()}", "Host": "pipeshubsim.zendesk.com"})
    assert resp.ok, (path, resp.status_code, resp.text[:200])
    return resp.json()


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimSync:

    async def test_tc_zdsim_sync_001_first_sync_equals_the_oracle(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await w.assert_matches_source("after the first sync")

    async def test_tc_zdsim_sync_002_custom_roles(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(custom_roles=True)))
        await w.assert_matches_source("custom roles, after the first sync")

    @pytest.mark.parametrize("plan", ["forbidden", "not_found"])
    async def test_tc_zdsim_sync_003_custom_roles_unavailable(self, zendesk_world: ZendeskWorld, plan: str) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(), behaviours={"custom_roles_plan": plan}))
        await w.assert_matches_source(f"custom roles {plan}")

    async def test_tc_zdsim_sync_005_no_help_center(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = desk()
        account["plan"] = {"help_center": False}
        assert succeeded(await w.start(account))
        await w.assert_matches_source("no help center")

    async def test_tc_zdsim_sync_006_unchanged_sync_changes_nothing(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        held = await w.holdings()
        assert succeeded(await w.sync())
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert before == after, "a sync with nothing changed created, removed or re-versioned a record"
        await w.assert_holdings_kept(held, "after a sync with nothing changed")

    async def test_tc_zdsim_sync_007_full_sync_rebuilds(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        before = {r["externalRecordId"]: r["id"] for r in await w.records()}
        assert succeeded(await w.sync(full=True))
        assert before == {r["externalRecordId"]: r["id"] for r in await w.records()}, \
            "a full sync changed the records' ids"
        await w.assert_matches_source("after a full sync")

    async def test_tc_zdsim_sync_008_seeded_records_equal_the_source(self, zendesk_world: ZendeskWorld, graph_provider) -> None:
        w = zendesk_world
        assert succeeded(await w.start(it_seed()))
        cid = w.connector_id
        ticket = _source(w, "/api/v2/tickets/4.json")["ticket"]
        record = await graph_provider.get_record_by_external_id(cid, "4")
        assert record is not None and record.record_type.value == "TICKET"
        assert record.record_name == ticket["subject"]
        assert f"/agent/tickets/{ticket['id']}" in (record.weburl or ""), record.weburl
        assert (record.weburl or "").startswith("https://pipeshubsim.zendesk.com/"), record.weburl
        assert record.source_created_at == _ms(ticket["created_at"])
        assert record.source_updated_at == _ms(ticket["updated_at"])
        typed = await graph_provider.get_typed_record_by_external_id(cid, "4")
        assert set(typed.labels) == set(ticket["tags"])
        assert typed.status.value == {"new": "NEW", "open": "OPEN", "pending": "PENDING", "hold": "BLOCKED",
                                      "solved": "RESOLVED", "closed": "CLOSED"}[ticket["status"]]
        group = _source(w, f"/api/v2/groups/{ticket['group_id']}.json")["group"]
        stored = await graph_provider.get_record_group_by_external_id(cid, f"group_{group['id']}")
        assert stored is not None and stored.name == group["name"]
        assert await graph_provider.record_belongs_to_external_group(cid, "4", f"group_{group['id']}")
        assert await graph_provider.group_has_record_permission(cid, f"group_{group['id']}", "4")
        assignee = _source(w, f"/api/v2/users/{ticket['assignee_id']}.json")["user"]
        user = await graph_provider.get_user_by_source_id(str(assignee["id"]), cid)
        assert user is not None and user.email.casefold() == assignee["email"].casefold()
        article = _source(w, "/api/v2/help_center/articles/39571438662930.json")["article"]
        stored_article = await graph_provider.get_record_by_external_id(cid, f"article_{article['id']}")
        assert stored_article is not None and stored_article.record_type.value == "WEBPAGE"
        assert stored_article.record_name == article["title"]
        section = _source(w, f"/api/v2/help_center/sections/{article['section_id']}.json")["section"]
        stored_section = await graph_provider.get_record_group_by_external_id(cid, f"section_{section['id']}")
        assert stored_section is not None and stored_section.name == section["name"]
        assert await graph_provider.record_belongs_to_external_group(
            cid, f"article_{article['id']}", f"section_{section['id']}")
        await w.assert_matches_source("seeded account")

    async def test_tc_zdsim_sync_009_streams_hold_public_content_only(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(it_seed()))
        ticket = _source(w, "/api/v2/tickets/4.json")["ticket"]
        comments = _source(w, "/api/v2/tickets/4/comments.json")["comments"]
        groups = await w.stream_groups("4")
        streamed = {str(g["source_group_id"]) for g in groups}
        assert {str(c["id"]) for c in comments if c["public"]} <= streamed
        assert not ({str(c["id"]) for c in comments if not c["public"]} & streamed), "an internal note was streamed"
        assert "INTERNAL-ONLY seed note" not in str([g.get("data") for g in groups])
        article = _source(w, "/api/v2/help_center/articles/39571438662930.json")["article"]
        body = await w.stream_groups(f"article_{article['id']}")
        assert body[0]["source_group_id"] == str(article["id"]) and body[0]["name"] == article["title"]
        assert body[0]["data"].strip()
        assert ticket["id"] == 4

    async def test_tc_zdsim_sync_010_ticket_reaches_indexing(self, zendesk_world: ZendeskWorld, graph_provider) -> None:
        w = zendesk_world
        assert succeeded(await w.start(it_seed()))
        await wait_until_record_indexing_completed(graph_provider, w.connector_id, "4", timeout=300,
                                                   description="Zendesk ticket indexing")

    async def test_tc_zdsim_sync_011_one_group_chosen(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = desk()
        w.load(account)
        billing = w.key("groups", "billing")
        assert succeeded(await w.start(account, filters=group_filter("in", [billing])))
        await w.assert_matches_source("only the billing group")
        assert all(r["externalGroupId"] in (f"group_{billing}",) or not r["externalRecordId"].isdigit()
                   for r in await w.records())

    async def test_tc_zdsim_sync_012_pages_of_two(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(real_clock=False),
                                       behaviours={"list_page_cap": 2, "incremental_page_size": 2}))
        w.ops(*bulk(17, 7, 5))
        assert succeeded(await w.sync())
        await w.assert_matches_source("pages of two, first sync")
        w.ops(*[{"op": "update_ticket", "ticket": n, "status": "pending"} for n in range(1, 8)])
        assert succeeded(await w.sync())
        await w.assert_matches_source("pages of two, second sync")

    async def test_tc_zdsim_sync_013_offset_only_lists(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(), behaviours={"list_page_cap": 3, "offset_only_lists": "ignore_cursor"}))
        w.ops(*bulk(9, 4, 3))
        assert succeeded(await w.sync())
        await w.assert_matches_source("every list offset-style")

    @pytest.mark.xfail(strict=True, reason=(
        "simulator: an empty time-based export answers end_time null, which its own reference (integer) rejects, so "
        "the simulator answers 500 and the run is not a read of Zendesk"))
    async def test_tc_zdsim_sync_014_export_holds_the_last_minute_back(self, zendesk_world: ZendeskWorld) -> None:
        """Zendesk's exports leave out the most recent minute: what an account made seconds ago holds comes with a later
        sync (a full sync keeps them: #6426, merged). One thing is asserted: the first sync's requests are all
        answered as Zendesk answers."""
        w = zendesk_world
        assert succeeded(await w.start(desk(), behaviours={"export_lag_s": 60}))
        assert not w.source_problems()

    async def test_tc_zdsim_sync_015_sections_list_without_nested_sections(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(), behaviours={"sections_list_includes_subsections": False}))
        await w.assert_matches_source("sections list without nested sections")

    async def test_tc_zdsim_sync_016_bulk_across_pages(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(real_clock=False),
                                       behaviours={"list_page_cap": 5, "incremental_page_size": 5}))
        w.ops(*bulk(30, 12, 8))
        assert succeeded(await w.sync())
        await w.assert_matches_source("bulk across pages")

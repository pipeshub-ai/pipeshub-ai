# pyright: ignore-file

"""
Zendesk on the simulator: who may read what
===========================================

The comparison of every sync checks every person against Zendesk's rules; the cases here pin the rules that the sync
cases do not exercise by themselves.

  TC-ZDSIM-ACL-001  A workspace member the connector does not register (no Zendesk account) finds a public article, as
                    Zendesk shows it to everyone. The connector grants the whole org but never links the org's All team
                    to the app, so they do not. Strict expected failure of the outcome; no open pull request [E]
  TC-ZDSIM-ACL-002  An internal comment's file is never readable by the requester; an agent of the group reads it [E, J]
  TC-ZDSIM-ACL-003  An agent whose role limits tickets (groups, assigned, organization) reads only those, with and without
                    custom roles; the light agent reads the groups' tickets [E]
  TC-ZDSIM-ACL-004  A suspended end user and a suspended agent read nothing of the tickets, and a restored one reads again [E]
  TC-ZDSIM-ACL-005  A segmented article and a draft are not stored, whoever may read them in Zendesk [E, B]
"""

import pytest
from connectors.zendesk.sim_instances import OUTSIDER, desk
from connectors.zendesk.sim_world import ZendeskWorld, succeeded


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimAccess:

    @pytest.mark.xfail(strict=True, reason="a member with no Zendesk account cannot find a public article: no ensure_team_app_edge (no open PR)")
    async def test_tc_zdsim_acl_001_member_without_a_zendesk_account_reads_public_articles(
            self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        article = await w.record_for(f"article_{w.key('articles', 'a_public')}")
        got = await w.graph.filter_accessible_record_ids([article["id"]], w.people[OUTSIDER], w.client.org_id)
        assert got, "a member with no Zendesk account cannot read a public article"

    async def test_tc_zdsim_acl_002_internal_files_stay_internal(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await w.assert_matches_source("after the first sync")
        records = {r["externalRecordId"] for r in await w.records()}
        internal = w.sim.expected()["tickets"][0]["comments"][2]["attachments"][0]["id"]
        assert not any(r.endswith(f"_attachment_{internal}") for r in records), "an internal comment's file was stored"

    async def test_tc_zdsim_acl_003_restricted_agents(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(custom_roles=True)))
        await w.assert_matches_source("agents of every ticket access")
        mine = await w.holdings()
        assigned = f"{w.key('tickets', 't_assigned')}"
        assert assigned in mine["agent-assigned@corp.example"]
        assert str(w.key("tickets", "t_secret")) not in mine["agent-billing@corp.example"]

    async def test_tc_zdsim_acl_004_suspended_people_and_back(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.ops({"op": "update_user", "user": "cust_shared1", "suspended": True},
              {"op": "update_user", "user": "agent_groups", "suspended": True})
        assert succeeded(await w.sync())
        await w.assert_matches_source("two people suspended")
        w.ops({"op": "update_user", "user": "cust_shared1", "suspended": False},
              {"op": "update_user", "user": "agent_groups", "suspended": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("both restored")

    async def test_tc_zdsim_acl_005_segmented_and_draft_articles_are_not_stored(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        stored = {r["externalRecordId"] for r in await w.records()}
        for article in w.sim.expected()["articles"]:
            if article["draft"] or article["segmented"]:
                assert f"article_{article['id']}" not in stored, f"{article['key']} was stored"

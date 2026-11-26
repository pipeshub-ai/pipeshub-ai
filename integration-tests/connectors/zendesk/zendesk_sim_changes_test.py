# pyright: ignore-file

"""
Zendesk on the simulator: changes after the first sync
======================================================

Every change in Zendesk shows up at the next sync, and rights that change without any ticket changing (a membership, a
role, a suspension, a group, an organization's sharing) take effect too: nobody keeps what Zendesk no longer shows them,
nobody lacks what it shows. Each step is a sync, then every comparison.

  TC-ZDSIM-CHG-001  Ticket changes: moved to another group, to a private group, out of its group; requester, assignee and
                    CCs change; a public comment opens an internal ticket to its requester; a new ticket; a deleted
                    ticket; a comment made internal [D, E]
  TC-ZDSIM-CHG-002  Rights taken away with no ticket change: a member leaves a group, an agent is suspended, deactivated
                    or downgraded, an organization stops (and starts again) sharing tickets, a group turns private or is
                    deleted, a customer leaves their organization or is suspended [E]
  TC-ZDSIM-CHG-003  The same for a desk with custom roles: a role loses its ticket access, an agent changes role, a role
                    gains public groups, an organization-limited agent moves on [E]
  TC-ZDSIM-CHG-004  Articles: edited, published from draft, restricted to a segment, moved to another section, a section
                    renamed and moved, a new article, a deleted article, an article back to draft [D]
  TC-ZDSIM-CHG-005  A deleted user's address given to a new account (end user, then an agent's address): the new account
                    reads its own tickets and nothing the earlier one could [E] (see also open pull request #5859)
  TC-ZDSIM-CHG-006  A change lands between two pages of the ticket export: the sync after has everything [H]
  TC-ZDSIM-CHG-007  A ticket and an article deleted in Zendesk while the connector is off are gone after it is turned on
                    again [F]
  TC-ZDSIM-CHG-008  A full sync removes what is gone at the source and keeps the rest [F]
  TC-ZDSIM-CHG-009  A Zendesk user with no PipesHub account holds tickets: nothing is lost, nobody else gains [E]
  TC-ZDSIM-CHG-010  A new comment with a file, an edited article body, a renamed ticket, a file redacted: the changes
                    arrive and the file records follow [D, J]
  TC-ZDSIM-CHG-011  A person with no e-mail address reads nothing and costs nothing [E]
  TC-ZDSIM-CHG-012  The platform's token for a suspended agent is still the connector's: an agent who gains the admin
                    role is not taken for one by the connector's own account check [E]
"""

from typing import Any

import pytest
from connectors.zendesk.sim_instances import desk
from connectors.zendesk.sim_world import ZendeskWorld, succeeded

TICKET_STEPS: list[tuple[str, list[dict[str, Any]]]] = [
    ("ticket moves to Billing", [{"op": "update_ticket", "ticket": "t_support", "group": "billing"}]),
    ("ticket moves to the private group", [{"op": "update_ticket", "ticket": "t_support", "group": "secret"}]),
    ("ticket leaves its group", [{"op": "update_ticket", "ticket": "t_support", "group": None}]),
    ("requester, assignee and CCs change", [
        {"op": "update_ticket", "ticket": "t_billing", "requester": "cust_none", "organization": None},
        {"op": "update_ticket", "ticket": "t_assigned", "assignee": "agent_all"},
        {"op": "update_ticket", "ticket": "t_cc", "followers": [], "collaborators": [], "email_ccs": []}]),
    ("a new public comment opens an internal ticket to its requester", [
        {"op": "add_comment", "ticket": "t_private_first", "author": "agent_all", "body": "Hello", "public": True}]),
    ("a new ticket", [{"op": "add_ticket", "key": "t_new", "subject": "New ticket", "requester": "cust_shared1",
                      "group": "support"}]),
    ("a ticket is deleted", [{"op": "delete_ticket", "ticket": "t_billing"}]),
    ("a comment is made internal", [{"op": "update_comment", "comment": "c_public", "public": False}]),
]

REVOCATIONS: list[tuple[str, list[dict[str, Any]]]] = [
    ("a member leaves a group", [{"op": "remove_membership", "user": "agent_groups", "group": "support"}]),
    ("an agent is suspended", [{"op": "update_user", "user": "agent_billing", "suspended": True}]),
    ("an agent is deactivated", [{"op": "update_user", "user": "agent_secret", "active": False}]),
    ("an agent is downgraded to assigned only", [{"op": "update_user", "user": "agent_all",
                                                  "ticket_restriction": "assigned"}]),
    ("an organization stops sharing tickets", [{"op": "update_org", "org": "org_shared", "shared_tickets": False}]),
    ("an organization shares again", [{"op": "update_org", "org": "org_shared", "shared_tickets": True}]),
    ("a group is turned private", [{"op": "update_group", "group": "billing", "is_public": False}]),
    ("a group is deleted", [{"op": "update_group", "group": "billing", "deleted": True}]),
    ("a customer leaves their organization", [{"op": "update_user", "user": "cust_shared2", "organization": None}]),
    ("a customer is suspended", [{"op": "update_user", "user": "cust_priv1", "suspended": True}]),
]

ROLE_STEPS: list[tuple[str, list[dict[str, Any]]]] = [
    ("a role loses its ticket access",
     [{"op": "update_custom_role", "role": "r_all", "ticket_access": "assigned-only"}]),
    ("an agent changes role", [{"op": "update_user", "user": "agent_groups", "custom_role": "r_org"}]),
    ("a role gains public groups",
     [{"op": "update_custom_role", "role": "r_groups", "ticket_access": "within-groups-and-public-groups"}]),
    ("an organization-limited agent moves on",
     [{"op": "update_user", "user": "agent_org", "organization": "org_shared"}]),
]

ARTICLE_STEPS: list[tuple[str, list[dict[str, Any]]]] = [
    ("an article is edited", [{"op": "update_article", "article": "a_public", "title": "Renamed article",
                               "body": "<p>NEW-BODY</p>"}]),
    ("a draft is published", [{"op": "update_article", "article": "a_draft", "draft": False}]),
    ("an article is restricted", [{"op": "update_article", "article": "a_sub", "segment": "seg_signed_in"}]),
    ("an article moves to another section", [{"op": "update_article", "article": "a_public", "section": "subsec"}]),
    ("a section moves and is renamed",
     [{"op": "update_section", "section": "subsec", "name": "Renamed section", "parent": None}]),
    ("a new article", [{"op": "add_article", "key": "a_new", "title": "Brand new", "section": "sec"}]),
    ("an article is deleted", [{"op": "delete_article", "article": "a_public"}]),
    ("an article goes back to draft", [{"op": "update_article", "article": "a_new", "draft": True}]),
]


async def run_steps(w: ZendeskWorld, steps: list[tuple[str, list[dict[str, Any]]]]) -> None:
    for label, operations in steps:
        w.ops(*operations)
        assert succeeded(await w.sync()), f"{label}: the sync did not succeed"
        await w.assert_matches_source(label)


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimChanges:

    async def test_tc_zdsim_chg_001_ticket_changes(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await run_steps(w, TICKET_STEPS)

    async def test_tc_zdsim_chg_002_rights_taken_away(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await run_steps(w, REVOCATIONS)

    async def test_tc_zdsim_chg_003_rights_of_custom_roles(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(custom_roles=True)))
        await run_steps(w, ROLE_STEPS)

    async def test_tc_zdsim_chg_004_article_lifecycle(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await run_steps(w, ARTICLE_STEPS)

    async def test_tc_zdsim_chg_005_address_passes_to_a_new_account(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await run_steps(w, [
            ("an end user's address goes to a new end user", [
                {"op": "update_user", "user": "cust_none", "active": False},
                {"op": "add_user", "key": "cust_none_new", "name": "Cust None New", "email": "none@customer.example"},
                {"op": "add_ticket", "key": "t_none_new", "subject": "From the new holder",
                 "requester": "cust_none_new", "group": "support"}]),
            ("an agent's address goes to a new end user", [
                {"op": "update_user", "user": "agent_billing", "active": False},
                {"op": "add_user", "key": "billing_new", "name": "Billing New", "email": "agent-billing@corp.example"},
                {"op": "add_ticket", "key": "t_billing_new", "subject": "From the new end user",
                 "requester": "billing_new", "group": "support"}])])

    async def test_tc_zdsim_chg_006_change_between_two_pages(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(), behaviours={"incremental_page_size": 2}))
        w.defer("/incremental/tickets", [
            {"op": "add_ticket", "key": "t_late", "subject": "Created mid-export", "requester": "cust_none",
             "group": "support"},
            {"op": "update_ticket", "ticket": "t_billing", "group": "support"}], after=2)
        w.ops(*[{"op": "update_ticket", "ticket": n, "priority": "high"} for n in range(1, 6)])
        assert succeeded(await w.sync())
        applied = w.sim.call("GET", "/_sim/zendesk/deferred")["applied"]
        assert len(applied) == 1, f"the change never landed between two pages: {applied}"
        assert succeeded(await w.sync())
        await w.assert_matches_source("the sync after the export was interrupted")

    async def test_tc_zdsim_chg_007_deleted_while_the_connector_is_off(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        w.ops({"op": "delete_ticket", "ticket": "t_billing"}, {"op": "delete_article", "article": "a_sub"},
              {"op": "update_ticket", "ticket": "t_secret", "group": "support"})
        assert succeeded(await w.enable())
        await w.assert_matches_source("after turning the connector on again")

    async def test_tc_zdsim_chg_008_full_sync_removes_what_is_gone(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.ops({"op": "delete_ticket", "ticket": "t_billing"}, {"op": "delete_article", "article": "a_sub"},
              {"op": "remove_membership", "user": "agent_billing", "group": "billing"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync of an account with deletions")

    async def test_tc_zdsim_chg_009_zendesk_user_without_a_pipeshub_account(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = desk()
        account["users"].append({"key": "zd_only", "name": "Zendesk Only", "email": "zd-only@corp.example",
                                 "role": "agent", "groups": ["support"], "ticket_restriction": "groups"})
        assert succeeded(await w.start(account))
        await w.assert_matches_source("an agent with no PipesHub account")
        held = await w.holdings()
        w.ops({"op": "add_ticket", "key": "t_zd", "subject": "For the agent with no account", "requester": "cust_none",
               "group": "support", "assignee": "zd_only"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a ticket assigned to an agent with no PipesHub account")
        assert set(held) == set(await w.holdings())

    async def test_tc_zdsim_chg_010_comment_with_a_file_and_edits(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.ops({"op": "add_attachment", "key": "att_new", "name": 'report "Q1" #2 <é>.txt', "content": "NEW-FILE"},
              {"op": "add_comment", "ticket": "t_billing", "author": "agent_billing", "body": "see file",
               "public": True, "attachments": ["att_new"]},
              {"op": "update_ticket", "ticket": "t_support", "subject": "Support ticket (renamed)"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a new comment with a file, a renamed ticket")
        w.ops({"op": "update_attachment", "attachment": "att_public", "deleted": True})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a file redacted")

    async def test_tc_zdsim_chg_011_person_without_an_email_address(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        expected = w.sim.expected()
        noemail = next(u for u in expected["users"] if u["key"] == "cust_noemail")
        assert noemail["email"] is None
        w.ops({"op": "add_ticket", "key": "t_ne2", "subject": "Again no e-mail", "requester": "cust_noemail",
               "group": "billing"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a second ticket of a requester with no e-mail address")

    async def test_tc_zdsim_chg_012_a_new_admin(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await run_steps(w, [("an agent becomes an admin", [{"op": "update_user", "user": "agent_billing",
                                                           "role": "admin"}])])

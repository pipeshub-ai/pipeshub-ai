# pyright: ignore-file

"""
Drupal Wiki on the simulator – who can find which page
======================================================

Each test changes who may read something in the simulated wiki, syncs, and checks that every person reads in PipesHub
exactly what the simulator's oracle (the wiki's own rules) lets them read, but for the gaps named in sim_world.py
(KNOWN_GAPS, or the ``known=`` of one call, each with the open pull request that fixes it: the call fails once the gap
no longer shows). The wiki's rules: a page is read by whoever may read its home space, and by whoever may read a space
it is shared into; an open space (anonymous or authenticated) is read by every enabled user, a private one by the
holders of any role on it, directly or through a group; a disabled user reads nothing.

  TC-DWSIM-ACL-001  First sync of a wiki with every kind of grant: a private space with a user role, a group role (its
                    members include a disabled user and one with no email), a nameless group, a disabled user's own
                    role, and a private space nobody belongs to
  TC-DWSIM-ACL-002  A user role on a private space added, then removed
  TC-DWSIM-ACL-003  A group role added; a member added to the group, then removed; the group deleted
  TC-DWSIM-ACL-004  A private space opened (authenticated), then closed again; an open one closed
  TC-DWSIM-ACL-005  A user disabled (a member of a private space and of a group), then enabled again
                    [#5799: a disabled account keeps reading what open spaces give everyone]
  TC-DWSIM-ACL-006  A user with no email gets one; someone with a PipesHub account and none in the wiki gets a wiki
                    account; a user's email changes to an address another person holds in PipesHub
  TC-DWSIM-ACL-007  A page shared into a private space, then an open one, then unshared
                    [#5799: a page shared into another space is read only from its home space]
  TC-DWSIM-ACL-008  A share into a space the sync leaves out gives the people there no read, and the oracle says so
  TC-DWSIM-ACL-009  A page moved into a private space and back, with and without the move changing lastModified
                    [#5799: a move that leaves lastModified alone is not seen by an incremental sync]
  TC-DWSIM-ACL-010  Someone with a PipesHub account and none in the wiki reads nothing, whatever a space's status
"""

import pytest
from connectors.drupal_wiki.sim_instances import (
    ALICE,
    ALICE_EMAIL,
    BOB,
    BOB_EMAIL,
    DAVE,
    ENG_SPACE,
    ENGINEERING,
    ERIN_EMAIL,
    FRANK_EMAIL,
    LEADERSHIP_PAGE,
    LEADERSHIP_SPACE,
    NEWBIE_EMAIL,
    OUTSIDER_EMAIL,
    PEOPLE_OPS,
    PEOPLE_SPACE,
    PRODUCT_SPACE,
    SVC,
)
from connectors.drupal_wiki.sim_world import DrupalWorld, gap, space_filter, succeeded

# Bob, disabled, still reads what the open spaces (Engineering, Product, Company Handbook) give the whole org.
GAP_DISABLED = {"LEAK:disabled-user": gap("#5799", users=[BOB_EMAIL], spaces=[1, 2, 4])}
# People Ops' page 301 and its attachment, shared into Product, are not read there.
GAP_SHARE = {"DENIED:shared-page": gap("#5799", records=["page:301", "attachment:5004"])}


async def _finance(w: DrupalWorld) -> dict[str, int]:
    """A private space with every kind of member, pages and an attachment; a private space nobody belongs to; ids."""
    frank, = (r["user"] for r in w.mutate(("user_create", {"email": FRANK_EMAIL, "first_name": "Frank"})))
    group, nameless = (r["group"] for r in w.mutate(
        ("group_create", {"name": "finance-readers", "members": [BOB, DAVE, SVC]}),
        ("group_create", {"name": None, "members": [ALICE]})))
    finance, vault = (r["space"] for r in w.mutate(
        ("space_create", {"name": "Finance", "access": "PRIVATE"}),
        ("space_create", {"name": "Empty vault", "access": "PRIVATE"})))
    w.mutate(("member", {"space": finance, "principal_type": "user", "principal_id": frank}),
             ("member", {"space": finance, "principal_type": "user", "principal_id": DAVE}),
             ("member", {"space": finance, "principal_type": "group", "principal_id": group}),
             ("member", {"space": finance, "principal_type": "group", "principal_id": nameless}))
    budget, vault_page = (r["page"] for r in w.mutate(
        ("page_create", {"space": finance, "title": "Budget 2027"}),
        ("page_create", {"space": vault, "title": "Nobody reads this"})))
    w.mutate(("page_create", {"space": finance, "title": "Forecast", "parent": budget}),
             ("attachment_add", {"page": budget, "file_name": "budget.csv", "media_type": "text/csv"}))
    return {"frank": frank, "group": group, "nameless": nameless, "finance": finance, "vault": vault,
            "budget": budget, "vault_page": vault_page}


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimAccess:

    async def test_tc_dwsim_acl_001_first_sync_every_kind_of_grant(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        await _finance(w)
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the first sync of a wiki with every kind of grant")

    async def test_tc_dwsim_acl_002_user_role_added_and_removed(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": ALICE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Alice was given a role on Leadership")
        w.mutate(("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": ALICE,
                             "add": False}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Alice's role on Leadership was removed")

    async def test_tc_dwsim_acl_003_group_role_group_members_and_group_deleted(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("member", {"space": LEADERSHIP_SPACE, "principal_type": "group", "principal_id": ENGINEERING}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the engineering group was given a role on Leadership")
        w.mutate(("group_member", {"group": ENGINEERING, "user": BOB}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Bob joined the engineering group")
        w.mutate(("group_member", {"group": ENGINEERING, "user": BOB, "add": False}),
                 ("group_member", {"group": ENGINEERING, "user": ALICE, "add": False}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Bob and Alice left the engineering group")
        w.mutate(("group_delete", {"id": PEOPLE_OPS}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the people-ops group was deleted")

    async def test_tc_dwsim_acl_004_space_opened_and_closed(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("space_update", {"id": PEOPLE_SPACE, "access": "AUTHENTICATED"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after People Ops was opened to every user")
        w.mutate(("space_update", {"id": PEOPLE_SPACE, "access": "PRIVATE"}),
                 ("space_update", {"id": ENG_SPACE, "access": "PRIVATE"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after People Ops was closed again and Engineering closed")

    async def test_tc_dwsim_acl_005_user_disabled_and_enabled(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("user_status", {"id": BOB, "status": "DISABLED"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Bob was disabled", known=GAP_DISABLED)
        w.mutate(("user_status", {"id": BOB, "status": "ENABLED"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Bob was enabled again")

    async def test_tc_dwsim_acl_006_email_changes_and_new_accounts(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("user_email", {"id": SVC, "email": ERIN_EMAIL}),
                 ("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": SVC}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the service account got an email and a role on Leadership")
        w.mutate(("user_create", {"email": NEWBIE_EMAIL, "first_name": "Newbie"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Newbie, who had a PipesHub account, got a wiki account")
        w.mutate(("user_email", {"id": ALICE, "email": "alice.renamed@example.com"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Alice's wiki email changed to one nobody has in PipesHub",
                                      known={"LEAK:no-wiki-account": gap("#5799", users=[ALICE_EMAIL], spaces=[1, 2, 4])})

    async def test_tc_dwsim_acl_007_share_in_and_out(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("page_share", {"id": 202, "space": PEOPLE_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a page of Product was shared into People Ops")
        w.mutate(("page_share", {"id": 202, "space": PEOPLE_SPACE, "add": False}),
                 ("page_share", {"id": 301, "space": PRODUCT_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after that share ended and a People Ops page was shared into Product",
                                      known=GAP_SHARE)
        w.mutate(("page_share", {"id": 301, "space": PRODUCT_SPACE, "add": False}),
                 ("page_share", {"id": 201, "space": ENG_SPACE, "add": False}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after every share ended")

    async def test_tc_dwsim_acl_008_share_through_a_space_the_sync_leaves_out(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(filters=space_filter("not_in", [PEOPLE_SPACE])))
        w.mutate(("page_share", {"id": LEADERSHIP_PAGE, "space": PEOPLE_SPACE}))
        assert succeeded(await w.sync())
        expected = w.sim.expected(w.scope)
        assert expected["pages"][str(LEADERSHIP_PAGE)]["readers_via_unsynced_shares"], \
            "the share must give People Ops readers a read"
        await w.assert_matches_source("after a page was shared into a space the sync leaves out")

    async def test_tc_dwsim_acl_009_page_moved_into_a_private_space_and_back(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"bumps_last_modified": ["edit", "attach", "move"]}))
        w.mutate(("page_move", {"id": 202, "space": LEADERSHIP_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a page moved into Leadership (the move changes lastModified)")
        w.mutate(("page_move", {"id": 202, "space": PRODUCT_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the page moved back")
        w.knobs(bumps_last_modified=["edit", "attach"])
        w.mutate(("page_move", {"id": 203, "space": LEADERSHIP_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a move that leaves lastModified alone", known={"PARENT": gap("#5799", records=["page:203"]),
                                                  "LEAK:other": gap("#5799", records=["page:203"])})

    async def test_tc_dwsim_acl_010_someone_with_no_wiki_account_reads_nothing(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        holdings = await w.holdings()
        assert holdings[OUTSIDER_EMAIL] == set(), f"a person with no wiki account reads {holdings[OUTSIDER_EMAIL]}"
        w.mutate(("space_update", {"id": LEADERSHIP_SPACE, "access": "ANONYMOUS"}))
        assert succeeded(await w.sync())
        assert (await w.holdings())[OUTSIDER_EMAIL] == set(), "an anonymous space is read by an outsider"
        await w.assert_matches_source("after Leadership became anonymous")

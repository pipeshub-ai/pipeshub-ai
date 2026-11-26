# pyright: ignore-file

"""
Drupal Wiki on the simulator – what the wiki's own variants change
===================================================================

Drupal Wiki's documentation leaves several behaviours open, and one wiki release refuses the internal GraphQL queries
the connector reads private-space members and the page tree with. The simulator has a switch for each (its knobs);
every test here sets the ones that change what the connector sees and checks the result against the oracle, which
applies the same switches. A wiki that refuses a query for a token (Drupal Wiki 8.44, ``graphql_mode``) leaves private
spaces unsynced unless the access an earlier sync confirmed is still fresh; the 30-day bound of that rule follows the
connector's own clock, which this suite cannot move, so it is a unit test (test_drupal_wiki_connector.py).

  TC-DWSIM-VAR-001  GraphQL refused from the start (refuse_pat, refuse_all, trusted_documents): open spaces sync in full,
                    private spaces and their pages are not stored, nobody gains, the admin is told; opened again, the
                    next sync matches
  TC-DWSIM-VAR-002  GraphQL refused after a sync: private spaces keep syncing under the access last confirmed, new
                    pages included; a member removed meanwhile keeps the stored access (a bounded over-share, told)
  TC-DWSIM-VAR-003  GraphQL answers 401, or a member query comes back partial (graphql_partial): the run does not
                    succeed, nothing is lost, and recovers
  TC-DWSIM-VAR-004  A page tree query that comes back partial or with only the root node (graphql_partial,
                    tree_root_node): pages keep their parent
  TC-DWSIM-VAR-005  Switches that change nothing the connector reads (graphql_strict, group_list_includes_members, members_include_disabled
                    off, page_order, tree_root_node, session_cookie, require_api_version): the sync matches the wiki
  TC-DWSIM-VAR-006  Shares: space_listing_includes_shared off, share_grants_read off
  TC-DWSIM-VAR-007  What moves lastModified (bumps_last_modified): edit only, and with move, reparent, share, trash and
                    restore: what an incremental sync sees, and what it cannot
  TC-DWSIM-VAR-008  Moving a page with its sub-pages or without them (children_follow_move)
  TC-DWSIM-VAR-009  Every space type, with every access status, holding a page
  TC-DWSIM-VAR-010  emails_visible off: nobody can be matched, so nobody reads anything, and nothing leaks
  TC-DWSIM-VAR-011  group_member_cap: a group whose member list is cut gives fewer readers, never more
  TC-DWSIM-VAR-012  A token whose owner reads only some spaces (sync_user_sees_all off), a hidden item answering 403 or 404
  TC-DWSIM-VAR-013  A wiki that lists nothing for a space (empty_listing) removes nothing
  TC-DWSIM-VAR-014  A wiki that caps the page size (max_page_size) below what the connector asks for, clamping or
                    refusing
"""

import pytest
from connectors.drupal_wiki.sim_instances import (
    ALICE,
    ALICE_EMAIL,
    BOB,
    ENG_SPACE,
    HANDBOOK_SPACE,
    LEADERSHIP_SPACE,
    PEOPLE_SPACE,
    PRODUCT_SPACE,
    TWO_FILES_PAGE,
)
from connectors.drupal_wiki.sim_world import SEED_SPECIAL_SPACES, DrupalWorld, gap, succeeded

GRAPHQL_ACTION = "drupal_wiki_graphql_refused"
PRIVATE_PAGES = {"page:301", "page:302", "page:303", "page:501", "page:502", "attachment:5004"}
ALL_BUMPS = ["edit", "attach", "move", "reparent", "share", "trash", "restore"]


def _action_codes(w: DrupalWorld) -> list[str]:
    return [item.get("code") for item in (w.connector().get("actionItems") or [])]


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimVariants:

    @pytest.mark.parametrize("mode", ["refuse_pat", "refuse_all", "trusted_documents"])
    async def test_tc_dwsim_var_001_graphql_refused_from_the_start(self, drupal_world: DrupalWorld, mode: str) -> None:
        w = drupal_world
        await w.start(knobs={"graphql_mode": mode})
        held = {r["externalRecordId"] for r in await w.records(parents=False)}
        assert not (held & PRIVATE_PAGES), f"private pages were stored although their members cannot be read: {held & PRIVATE_PAGES}"
        assert {f"page:{i}" for i in (101, 201, 401, 402)} <= held, "open spaces must sync in full"
        assert GRAPHQL_ACTION in _action_codes(w), f"the admin was not told ({_action_codes(w)})"
        holdings = await w.holdings()
        wiki = w.oracle_holdings()
        assert all(holdings[e] <= wiki[e] for e in wiki), "someone reads more than the wiki lets them"
        w.knobs(graphql_mode="open")
        # The pages stored while the tree was unreadable have no parent; an incremental sync does not list them again
        # (no lastModified moved), so a full sync places them.
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the wiki answered the member query again")
        assert GRAPHQL_ACTION not in _action_codes(w), "the admin's notice stayed after the wiki answered"

    async def test_tc_dwsim_var_002_graphql_refused_after_a_sync(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.knobs(graphql_mode="refuse_pat")
        w.mutate(("page_create", {"space": PEOPLE_SPACE, "title": "Added while members are refused"}),
                 ("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": ALICE}),
                 ("member", {"space": PEOPLE_SPACE, "principal_type": "user", "principal_id": BOB, "add": False}))
        await w.sync()
        after = await w.assert_kept_not_overshared(before, "after the member query was refused")
        assert any("page:10" in e or e.startswith("page:1") for e in after["records"])
        assert GRAPHQL_ACTION in _action_codes(w), f"the admin was not told ({_action_codes(w)})"
        # The access stored for the private spaces still applies: Alice was not given Leadership, Bob was not taken
        # out of People Ops (the wiki's rule for a refused member query: keep what was confirmed).
        assert after[ALICE_EMAIL] == before[ALICE_EMAIL] or not (after[ALICE_EMAIL] - before[ALICE_EMAIL]) & PRIVATE_PAGES
        w.knobs(graphql_mode="open")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the wiki answered the member query again")

    @pytest.mark.parametrize("knobs", [{"graphql_auth_ok": False}, {"graphql_partial": ["spaceRoleUserMembers"]},
                                       {"graphql_partial": ["spaceRoleGroupMembers"]}])
    async def test_tc_dwsim_var_003_graphql_that_errors(self, drupal_world: DrupalWorld, knobs: dict) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.mutate(("page_create", {"space": PEOPLE_SPACE, "title": "Added while members cannot be read"}))
        w.knobs(**knobs)
        outcome = await w.sync()
        assert not succeeded(outcome), f"a sync whose member queries failed ({knobs}) reported success"
        await w.assert_kept_not_overshared(before, f"after a sync whose member queries failed ({knobs})")
        w.knobs(graphql_auth_ok=True, graphql_partial=[])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the member queries answered again")

    async def test_tc_dwsim_var_004_page_tree_comes_back_partial(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.knobs(graphql_partial=["spaceContentStructureFlatTree"])
        w.mutate(("page_create", {"space": ENG_SPACE, "title": "Under a page", "parent": 101}),
                 ("page_edit", {"id": 105, "body_extra": "<p>edited</p>"}))
        outcome = await w.sync()
        assert not succeeded(outcome), "a sync that could not read the page tree reported success"
        records = {r["externalRecordId"]: r for r in await w.records()}
        # The tree could not be read: stored parents stay, and no page that was edited lost its parent.
        assert records["page:105"]["parent"] == "page:104", "an edited page lost its parent when the tree was unreadable"
        assert records["page:103"]["parent"] == "page:102"
        await w.assert_kept_not_overshared(before, "after a sync that could not read the page tree")
        w.knobs(graphql_partial=[])
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the page tree could be read again")

    async def test_tc_dwsim_var_004_page_tree_with_a_root_node(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"tree_root_node": True}))
        w.mutate(("page_create", {"space": ENG_SPACE, "title": "Under a page", "parent": 101}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a sync with a root node in the page tree")

    async def test_tc_dwsim_var_005_switches_that_change_nothing_the_connector_reads(
            self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        knobs = {"group_list_includes_members": True, "members_include_disabled": False, "page_order": "modified_desc",
                 "tree_root_node": True, "session_cookie": True, "require_api_version": True,
                 "graphql_strict": True}
        assert succeeded(await w.start(knobs=knobs))
        await w.assert_matches_source("after the first sync")
        w.mutate(("page_create", {"space": ENG_SPACE, "title": "Child", "parent": 104}),
                 ("group_member", {"group": 12, "user": BOB}),
                 ("user_status", {"id": 6, "status": "ENABLED"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after changes under those switches")
        w.knobs(page_order="modified")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync with another page order")

    async def test_tc_dwsim_var_006_shares(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"space_listing_includes_shared": False, "share_grants_read": False}))
        w.mutate(("page_share", {"id": 301, "space": PRODUCT_SPACE}), ("page_share", {"id": 202, "space": PEOPLE_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after shares in a wiki whose shares give no read and are not listed")

    async def test_tc_dwsim_var_007_what_moves_last_modified(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"bumps_last_modified": ALL_BUMPS, "trash_extension": True}))
        w.mutate(("page_move", {"id": 203, "space": HANDBOOK_SPACE}), ("page_reparent", {"id": 107, "parent": 101}),
                 ("page_delete", {"id": 402, "mode": "ui"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a move, a re-parent and a trash, each changing lastModified",
                                      known={"STALE": gap("#5799", records=["page:402"]),
                                             "LEAK:stale-record": gap("#5799", records=["page:402"])})
        w.mutate(("page_restore", {"id": 402}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the restore, which changes lastModified")

    @pytest.mark.xfail(strict=True, reason="a re-parent that leaves lastModified alone is never seen by an incremental "
                                           "sync: no open pull request")
    async def test_tc_dwsim_var_007_reparent_that_leaves_last_modified_alone(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"bumps_last_modified": ["edit"]}))
        w.mutate(("page_reparent", {"id": 108, "parent": 101}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a re-parent that leaves lastModified alone")

    @pytest.mark.parametrize("follow", [
        True,
        pytest.param(False, marks=pytest.mark.xfail(
            strict=True, reason="sub-pages whose parent changes without their own lastModified moving keep the old "
                                "parent: no open pull request")),
    ])
    async def test_tc_dwsim_var_008_move_with_or_without_sub_pages(self, drupal_world: DrupalWorld, follow: bool) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"children_follow_move": follow,
                                              "bumps_last_modified": ["edit", "attach", "move"]}))
        w.mutate(("page_move", {"id": 101, "space": HANDBOOK_SPACE}))
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after a page with sub-pages moved (children follow: {follow})")

    async def test_tc_dwsim_var_009_every_space_type_and_access_status(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        types = ["BASIC", "CONTAINER", "CORPORATEBLOG", "CORPORATENEWS", "USERSPACE", "SYSTEMSPACE",
                 "CORPORATENEWSDRAFTS", "DESKTOP"]
        odd_spaces: list[int] = []
        odd_names: set[str] = set(SEED_SPECIAL_SPACES)
        content = {"BASIC", "CONTAINER", "CORPORATEBLOG", "CORPORATENEWS"}
        for kind in types:
            for status in ("PRIVATE", "AUTHENTICATED", "ANONYMOUS"):
                space, = (r["space"] for r in w.mutate(("space_create", {"name": f"{kind} {status}", "type": kind,
                                                                         "access": status})))
                if kind not in content:
                    odd_spaces.append(space)
                    odd_names.add(f"{kind} {status}")
                w.mutate(("page_create", {"space": space, "title": f"A page of {kind} {status}"}),
                         ("member", {"space": space, "principal_type": "user", "principal_id": BOB}))
        assert succeeded(await w.enable())
        # Only the spaces that are not content spaces are synced although the wiki's rules leave them out.
        await w.assert_matches_source("after the first sync of every kind of space", known={
            "STALE": gap("#5799", spaces=odd_spaces), "LEAK:stale-record": gap("#5799", spaces=odd_spaces),
            "STALE-GROUP": gap("#5799", names=odd_names)})

    async def test_tc_dwsim_var_010_emails_hidden(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        await w.start(knobs={"emails_visible": False})
        holdings = await w.holdings()
        over = {e: sorted(v) for e, v in holdings.items() if e != "records" and v}
        assert not over, f"nobody can be matched without an email, yet {over} read"
        w.knobs(emails_visible=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the wiki showed its users' emails")

    async def test_tc_dwsim_var_011_group_member_list_cut(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        await w.start(knobs={"group_member_cap": 1})
        over = [p for p in await w.differences() if p["type"].startswith("LEAK")]
        assert not over, f"a cut member list gave a read the wiki does not: {over[:5]}"
        w.knobs(group_member_cap=None)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the member lists were whole")

    @pytest.mark.parametrize("status", [403, 404])
    async def test_tc_dwsim_var_012_token_owner_reads_only_some_spaces(self, drupal_world: DrupalWorld,
                                                                      status: int) -> None:
        w = drupal_world
        outcome = await w.start(knobs={"sync_user_sees_all": False, "hidden_status": status})
        if succeeded(outcome):
            await w.assert_matches_source(f"after a sync of what the token's owner reads (hidden answers {status})")
        else:
            over = [p for p in await w.differences() if p["type"].startswith("LEAK")]
            assert not over, f"a sync that did not succeed gave reads the wiki does not: {over[:5]}"

    async def test_tc_dwsim_var_013_a_listing_that_says_nothing(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.knobs(empty_listing=[ENG_SPACE])
        w.mutate(("page_edit", {"id": 101, "body_extra": "<p>changed</p>"}))
        await w.sync()
        await w.assert_kept_not_overshared(before, "after a wiki listed nothing for a space")
        w.knobs(empty_listing=[])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the listing answered again")

    async def test_tc_dwsim_var_014_page_size_cap_refusing(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.knobs(max_page_size=50, oversize="reject")
        w.mutate(("page_edit", {"id": 101, "body_extra": "<p>changed</p>"}))
        outcome = await w.sync()
        assert not succeeded(outcome), "a wiki that refuses the page size the connector asks for reported success"
        await w.assert_kept_not_overshared(before, "after a wiki refused the page size")
        w.knobs(max_page_size=2000, oversize="clamp")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the wiki accepted the page size")

    @pytest.mark.xfail(strict=True, reason="a page size cap below the connector's 100 leaves attachments unread: "
                                           "no open pull request")
    async def test_tc_dwsim_var_014_page_size_cap_clamping(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        w.mutate(*[("attachment_add", {"page": TWO_FILES_PAGE, "file_name": f"more-{i}.txt", "content": "x"})
                   for i in range(4)])
        w.knobs(max_page_size=3)
        assert succeeded(await w.enable())
        await w.assert_matches_source("after a wiki that clamps every page to three rows")

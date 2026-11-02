# pyright: ignore-file

"""
BookStack on the simulator – who can open what
==============================================

Each test changes who may view something in the simulated BookStack, syncs, and checks that every person opens
in PipesHub exactly what the simulator's oracle (BookStack's own permission rules) says they may view
(sim_world.py). The finding each test guards is in brackets; it fails on upstream main and passes with the
audit's fixes.

  TC-BSSIM-ACL-001  A role that may view books but only its own pages finds no one else's page [F-PAGE-ACCESS]
  TC-BSSIM-ACL-002  An explicit deny on one of a user's roles beats another role's permission [F-MULTIROLE-DENY]
  TC-BSSIM-ACL-003  Joining a denied role later takes the page away [F-MULTIROLE-DENY]
  TC-BSSIM-ACL-004  Losing page-view-all takes the role's pages away though it still sees the books [F-PAGE-ACCESS]
  TC-BSSIM-ACL-005  A user deleted in BookStack loses access [F-USER-DELETE]
  TC-BSSIM-ACL-006  A pure permission change on a page updates access
"""

import pytest
from connectors.bookstack.sim_instances import DENY_ALL, base_instance, plain_instance
from connectors.bookstack.sim_world import BookStackWorld, succeeded


@pytest.mark.integration
@pytest.mark.bookstack_sim
@pytest.mark.asyncio(loop_scope="session")
class TestBookStackSimAccess:

    async def test_tc_bssim_acl_001_book_view_is_not_page_view(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(base_instance()))
        await w.assert_matches_source("Frank's role views books and only his own pages")

    async def test_tc_bssim_acl_002_explicit_deny_beats_role_permission(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(base_instance()))
        await w.assert_matches_source("Gina is in Readers and in Viewer, which is denied one page")

    async def test_tc_bssim_acl_003_joining_a_denied_role(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "update_user", "id": "gina", "data": {"roles": ["@role:readers", "@role:viewer"]}})
        await w.sync()
        await w.assert_matches_source("after Gina joined Viewer")

    async def test_tc_bssim_acl_004_role_loses_page_view(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "update_role", "id": "role:readers", "data": {"permissions": ["book-view-all"]}})
        await w.sync()
        await w.assert_matches_source("after Readers kept book-view-all but lost page-view-all")

    async def test_tc_bssim_acl_005_deleted_user(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "delete_user", "id": "carol"})
        await w.sync()
        await w.assert_matches_source("after Carol was deleted from BookStack")

    async def test_tc_bssim_acl_006_pure_permission_change(self, bookstack_world: BookStackWorld) -> None:
        w = bookstack_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "set_permissions", "kind": "page", "id": "welcome",
               "data": {"role_permissions": [{"role_id": "@role:editor", "view": True, "create": False,
                                              "update": False, "delete": False}],
                        "fallback_permissions": {"inheriting": False, **DENY_ALL}}})
        await w.sync()
        await w.assert_matches_source("after Welcome was restricted to Editors")

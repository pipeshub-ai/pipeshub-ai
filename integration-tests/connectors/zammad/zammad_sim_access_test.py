# pyright: ignore-file

"""
Zammad on the simulator – who can find which ticket
===================================================

Each test changes who may read something in the simulated Zammad, syncs, and checks that every person reads in
PipesHub exactly what the simulator's oracle (Zammad's own rules) lets them read, but for the gaps sim_world.KNOWN_GAPS
names (sim_world.py). A group's readers are the active users holding ticket.agent with read or full on it, directly or
through an active role; change, create or overview alone give nothing. The finding each test guards is in brackets;
it fails on code that treats every access level as membership.

  TC-ZMSIM-ACL-001  First sync: agents with change, create or overview only find nothing; an agent reaching a group
                    through a role finds its tickets; the inactive group and the inactive agent give nothing; an
                    internal article's attachment reaches agent readers only [F-ZAMMAD-ACCESS]
  TC-ZMSIM-ACL-002  A changed access level: read lowered to overview takes the group away, change raised to read
                    gives it, full on another group gives that one [F-ZAMMAD-ACCESS]
  TC-ZMSIM-ACL-003  A deactivated agent loses every ticket; reactivated, gets them back
  TC-ZMSIM-ACL-004  Role group access: a new role's read gives its group; a role lowered to change gives nothing; a
                    deactivated role gives nothing [F-ZAMMAD-ROLE-ACCESS]
  TC-ZMSIM-ACL-005  A deactivated group: its tickets leave PipesHub and nobody reads them; reactivated, they come back
  TC-ZMSIM-ACL-006  An agent added with read on a group finds its tickets on the next sync
"""

import pytest
from connectors.zammad.sim_instances import base_instance, plain_instance
from connectors.zammad.sim_world import ZammadWorld, succeeded


@pytest.mark.integration
@pytest.mark.zammad_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZammadSimAccess:

    async def test_tc_zmsim_acl_001_first_sync_access_matrix(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance()))
        await w.assert_matches_source("after the first sync of an installation with every kind of access")

    async def test_tc_zmsim_acl_002_access_level_change(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance()))
        w.ops({"op": "set_group_access", "user": "ben", "group": "support", "access": ["overview"]},
              {"op": "set_group_access", "user": "cy", "group": "support", "access": ["read"]},
              {"op": "set_group_access", "user": "dee", "group": "billing", "access": ["full"]})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Ben was lowered to overview, Cy raised to read and Dee given Billing")

    async def test_tc_zmsim_acl_003_deactivated_agent(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance()))
        w.ops({"op": "deactivate_user", "user": "ann"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Ann was deactivated")
        w.ops({"op": "activate_user", "user": "ann"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Ann was reactivated")

    async def test_tc_zmsim_acl_004_role_group_access(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance()))
        w.ops({"op": "create_role", "key": "support_readers", "name": "Support readers", "permissions": ["ticket.agent"]},
              {"op": "set_role_group_access", "role": "support_readers", "group": "support", "access": ["read"]},
              {"op": "set_user_roles", "user": "dee", "roles": ["Agent", "support_readers"]},
              {"op": "set_role_group_access", "role": "billing_agents", "group": "billing", "access": ["change"]})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Dee joined a role that reads Support and Billing agents fell to change")
        w.ops({"op": "set_role_group_access", "role": "billing_agents", "group": "billing", "access": ["full"]},
              {"op": "update_role", "role": "billing_agents", "data": {"active": False}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the Billing agents role was given full access and deactivated")

    async def test_tc_zmsim_acl_005_deactivated_group(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(base_instance()))
        w.ops({"op": "update_group", "group": "support", "data": {"active": False}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the Support group was deactivated")
        w.ops({"op": "update_group", "group": "support", "data": {"active": True}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the Support group was reactivated")

    async def test_tc_zmsim_acl_006_new_agent(self, zammad_world: ZammadWorld) -> None:
        w = zammad_world
        assert succeeded(await w.start(plain_instance()))
        w.ops({"op": "create_user", "key": "newbie", "email": "newbie@example.com", "firstname": "Newbie",
               "roles": ["Agent"], "groups": {"support": ["read"]}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a new agent was given read on Support")

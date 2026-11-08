# pyright: ignore-file

"""
ServiceNow on the simulator – access changes in the instance reach PipesHub
===========================================================================

  TC-SNSIM-ACC-001  A user deactivated in ServiceNow can no longer open what they read before
  TC-SNSIM-ACC-002  Joining the group a knowledge base's Can Read criterion names lets a user open its articles;
                    leaving it takes them away again
  TC-SNSIM-ACC-003  Turning glide.knowman.block_access_with_no_user_criteria off opens a knowledge base with no
                    Can Read and no Can Contribute criteria to everyone; turning it on again closes it
"""

import pytest
from connectors.servicenow.sim_world import ServiceNowWorld, succeeded


@pytest.mark.integration
@pytest.mark.servicenow_sim
@pytest.mark.asyncio(loop_scope="session")
class TestServiceNowSimAccess:

    async def test_tc_snsim_acc_001_deactivated_user(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        expected = w.sim.oracle()
        sys_id_of = {u["email"].lower(): u["sys_id"] for u in expected["users"] if u["email"]}
        # A probed person who reads some article but not every one: not an admin, whose access no criterion gives.
        counts = {email: sum(sys_id_of[email] in a["readers"] for a in expected["articles"]) for email in w.people}
        email = min((e for e, n in counts.items() if 0 < n < len(expected["articles"])), key=lambda e: (-counts[e], e))
        w.sim.mutate({"op": "record_set", "args": {"table": "sys_user", "sys_id": sys_id_of[email], "active": "false"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after {email} was deactivated")

    async def test_tc_snsim_acc_002_group_membership(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        sim = w.sim
        by_group = {c["sys_id"]: c["group"] for c in sim.table("user_criteria", "active=true^groupISNOTEMPTY",
                                                               "sys_id,group")
                    if "," not in c["group"]}
        kbs = {m["kb_knowledge_base"]: by_group[m["user_criteria"]]
               for m in sim.table("kb_uc_can_read_mtom", "", "kb_knowledge_base,user_criteria")
               if m["user_criteria"] in by_group}
        expected = sim.oracle()
        sys_id_of = {u["email"].lower(): u["sys_id"] for u in expected["users"] if u["email"]}
        readers_of_kb = {k["sys_id"]: set(k["readers"]) for k in expected["knowledge_bases"] if k["active"]}
        kb, group = next((k, g) for k, g in sorted(kbs.items()) if k in readers_of_kb)
        email = next(e for e in sorted(w.people) if sys_id_of[e] not in readers_of_kb[kb])
        added = sim.mutate({"op": "member", "args": {"group": group, "user": sys_id_of[email]}})
        assert succeeded(await w.sync())
        assert sys_id_of[email] in {r for k in sim.oracle()["knowledge_bases"] if k["sys_id"] == kb for r in k["readers"]}
        await w.assert_matches_source(f"after {email} joined the group of knowledge base {kb}")
        membership = sim.table("sys_user_grmember", f"group={group}^user={sys_id_of[email]}", "sys_id")
        assert membership, f"the membership the test added is not there: {added}"
        sim.mutate({"op": "record_delete", "args": {"table": "sys_user_grmember", "sys_id": membership[0]["sys_id"]}})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after {email} left the group again")

    async def test_tc_snsim_acc_003_no_criteria_property(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        # The knowledge bases with no Can Read criteria lose their Can Contribute ones too: with neither, the
        # simulator and the connector agree that turning the property off opens them to everyone. With Can
        # Contribute only they differ (the simulator opens, the connector keeps closed) and no instance has
        # settled it, so that case is not asserted.
        with_read = {m["kb_knowledge_base"] for m in w.sim.table("kb_uc_can_read_mtom", "", "kb_knowledge_base")}
        open_ops = [{"op": "kb_criterion", "args": {"kb": m["kb_knowledge_base"], "criterion": m["user_criteria"],
                                                    "kind": "can_contribute", "add": False}}
                    for m in w.sim.table("kb_uc_can_contribute_mtom", "", "kb_knowledge_base,user_criteria")
                    if m["kb_knowledge_base"] not in with_read]
        assert open_ops, "the instance has no knowledge base without Can Read criteria"
        w.sim.mutate(*open_ops)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the Can Contribute criteria were removed")
        prop = "glide.knowman.block_access_with_no_user_criteria"
        w.sim.mutate({"op": "property_set", "args": {"name": prop, "value": "false"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after {prop} was turned off")
        w.sim.mutate({"op": "property_set", "args": {"name": prop, "value": "true"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"after {prop} was turned on again")

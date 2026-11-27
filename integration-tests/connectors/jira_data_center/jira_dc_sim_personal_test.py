# pyright: ignore-file

"""
Jira Data Center Personal on the simulator – one person's Jira
==============================================================

The personal connector signs in as the person who set it up (their own token), syncs what Jira shows that account,
and shows it to that person alone: no permission API is read, no one else gains anything.

  TC-JDSIM-PERS-001  First sync: the records are exactly the issues (and their attachments) Jira shows the account; the
                     creator reads all of them and every other PipesHub user none
  TC-JDSIM-PERS-002  The account loses a project: its records leave; gains one: they arrive
  TC-JDSIM-PERS-003  An issue deleted at the source leaves; the creator's reading is unchanged for the rest
  TC-JDSIM-PERS-004  Deleting the connector (unlink) leaves nothing behind
  TC-JDSIM-PERS-005  A refused credential (401) and a failing search (503): the run is not a success and keeps what was
                     there; the next clean run matches
  TC-JDSIM-PERS-006  The full sync's unlink under faults: with issues deleted at the source and the account check
                     (/myself), the project list or the issue search refused (401, 403, 500, dropped connection), a full
                     sync removes only what Jira deleted, never an issue that still exists; the next clean full sync
                     removes what is gone
"""

import pytest
from connectors.jira_data_center.sim_world import (
    CREATOR_EMAIL,
    FILE_PREFIX,
    JiraDcWorld,
    drop,
    fault,
    succeeded,
)


async def _assert_personal(w: JiraDcWorld, account_email: str, label: str) -> None:
    expected = w.sim.expected()
    mine = {str(i) for i in w.sim.access(account_email)["jira_ids"]}
    want = {tid for tid in expected["ticket_index"] if tid in mine}
    want |= {f"{FILE_PREFIX}{fid}" for tid in want for fid in expected["ticket_index"][tid]["files"]}
    records = await w.records()
    have = {str(r["externalRecordId"]) for r in records}
    assert have == want, f"{label}: records differ from what Jira shows the account: missing {sorted(want - have)[:10]}, extra {sorted(have - want)[:10]}"
    readable = await w.readable(records)
    for email, seen in readable.items():
        if email.lower() == CREATOR_EMAIL.lower():
            assert len(seen) == len(records), f"{label}: the creator reads {len(seen)} of {len(records)} records"
        else:
            assert not seen, f"{label}: {email} reads {len(seen)} records of someone else's personal connector"


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimPersonal:

    def _account(self, w: JiraDcWorld) -> tuple[str, str]:
        user = next(u for u in w.sim.users() if u["username"] == w.usernames()[4])
        return user["username"], user["email"]

    async def test_tc_jdsim_pers_001_first_sync(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        username, email = self._account(w)
        assert succeeded(await w.start(personal=True, user=username, reset=False))
        await _assert_personal(w, email, "after the first sync")

    async def test_tc_jdsim_pers_002_project_lost_and_gained(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        username, email = self._account(w)
        assert succeeded(await w.start(personal=True, user=username, reset=False))
        w.mutate({"op": "grant", "project": "LIGH", "holder": "user", "parameter": username})
        assert succeeded(await w.sync())
        await _assert_personal(w, email, "after the account gained LIGH")
        w.mutate({"op": "grant", "project": "LIGH", "holder": "user", "parameter": username, "present": False},
                 {"op": "project", "project": "EVER", "status": "archived"})
        assert succeeded(await w.sync())
        await _assert_personal(w, email, "after the account lost LIGH and EVER was archived")

    async def test_tc_jdsim_pers_003_issue_deleted(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        username, email = self._account(w)
        assert succeeded(await w.start(personal=True, user=username, reset=False))
        w.sim.advance(delete=3)
        assert succeeded(await w.sync())
        await _assert_personal(w, email, "after three issues were deleted")

    async def test_tc_jdsim_pers_004_unlink(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        username, _ = self._account(w)
        assert succeeded(await w.start(personal=True, user=username, reset=False))
        connector_id = w.connector_id
        w.disable()
        await w._wait_idle()
        w.client.delete_connector(connector_id)
        w.connector_id = None
        await w.graph.assert_connector_graph_fully_cleaned(connector_id)

    @pytest.mark.parametrize("code", [401, 503])
    async def test_tc_jdsim_pers_005_failures(self, jira_dc_world: JiraDcWorld, code: int) -> None:
        w = jira_dc_world
        w.sim.reset()
        username, email = self._account(w)
        assert succeeded(await w.start(personal=True, user=username, reset=False))
        before = await w.holdings()
        w.sim.advance(delete=2)
        w.faults(fault(r"^/rest/api/2/(search|project|myself)$", code, max_times=500))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), f"a personal sync whose reads all answered {code} reported success"
        await w.assert_holdings_kept(before, f"after a personal sync that met {code}")
        assert succeeded(await w.sync())
        await _assert_personal(w, email, "after Jira answered again")

    async def test_tc_jdsim_pers_006_full_sync_unlink_under_faults(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        username, email = self._account(w)
        assert succeeded(await w.start(personal=True, user=username, reset=False))
        mine = {str(i) for i in w.sim.access(email)["jira_ids"]}
        index = w.sim.expected()["ticket_index"]
        deleted = w.sim.advance(delete=4)["changes"]["delete"]
        gone = {str(d["issue_id"]) for d in deleted}
        gone_files = {f"attachment_{f}" for tid in gone if tid in index for f in index[tid]["files"]}
        before = {r["externalRecordId"] for r in await w.records()}
        assert gone & before, "no deleted issue was among the personal records"
        endpoints = {"/myself": r"^/rest/api/2/myself$", "/project": r"^/rest/api/2/project$",
                     "/search": r"^/rest/api/2/search$"}
        kinds = {"401": lambda rx: fault(rx, 401, max_times=60), "403": lambda rx: fault(rx, 403, max_times=60),
                 "500": lambda rx: fault(rx, 500, max_times=60), "drop": lambda rx: drop(rx, max_times=60)}
        for name, rx in endpoints.items():
            for kind, build in kinds.items():
                w.faults(build(rx))
                await w.sync(full=True)
                w.clear_faults()
                after = {r["externalRecordId"] for r in await w.records()}
                removed = before - after
                kept_wrongly = removed - gone - gone_files
                assert not kept_wrongly, f"{kind} on {name}: a full sync removed records of issues that still exist: {sorted(kept_wrongly)[:5]}"
                before = before & after
        assert succeeded(await w.sync(full=True))
        await _assert_personal(w, email, "after a clean full sync")
        assert mine

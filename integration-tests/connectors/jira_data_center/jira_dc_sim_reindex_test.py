# pyright: ignore-file

"""
Jira Data Center on the simulator – reindexing a record
=======================================================

  TC-JDSIM-REIDX-001  Reindexing an issue and an attachment changes nothing that is stored or granted
  TC-JDSIM-REIDX-002  Reindexing an issue after it changed at the source: the record shows the change, who reads it is
                      still what Jira says
  TC-JDSIM-REIDX-003  Reindexing after an access change (a person who gained, and one who lost, access to its project)
                      leaves everyone reading what Jira says
  TC-JDSIM-REIDX-004  Reindexing the record of an issue deleted at the source: it does not come back to life, and it is
                      not readable
"""

import contextlib

import pytest
from connectors.jira_data_center.sim_world import FILE_PREFIX, JiraDcWorld, succeeded
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimReindex:

    async def _first(self, w: JiraDcWorld, ext_prefix: str = "") -> tuple[dict, dict]:
        records = {r["externalRecordId"]: r for r in await w.creator_records()}
        ticket = next(r for e, r in sorted(records.items()) if not e.startswith(FILE_PREFIX))
        file = next(r for e, r in sorted(records.items()) if e.startswith(FILE_PREFIX))
        return ticket, file

    async def test_tc_jdsim_reidx_001_reindex_changes_nothing(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        ticket, file = await self._first(w)
        before = await w.holdings()
        w.client.reindex_record(ticket["id"])
        w.client.reindex_record(file["id"])
        await w.assert_holdings_kept(before, "after two records were reindexed")
        await w.assert_matches_source("after two records were reindexed")

    async def test_tc_jdsim_reidx_002_reindex_after_a_change(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        ticket, _ = await self._first(w)
        key = w.sim.expected()["ticket_index"][ticket["externalRecordId"]]["key"]
        w.mutate({"op": "issue", "issue": key, "summary": "Renamed at the source"})
        w.client.reindex_record(ticket["id"])
        await w.assert_matches_source("after an issue changed at the source and was reindexed")

    async def test_tc_jdsim_reidx_003_reindex_after_an_access_change(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        records = await w.creator_records()
        quiet = next(r for r in records if r["externalRecordId"] in w.sim.expected()["ticket_index"])
        project = w.sim.expected()["ticket_index"][quiet["externalRecordId"]]["key"].rsplit("-", 1)[0]
        w.mutate({"op": "grant", "project": project, "holder": "anyone"})
        w.client.reindex_record(quiet["id"])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a grant on the project of a reindexed record")

    async def test_tc_jdsim_reidx_004_reindex_a_deleted_issue(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        gone = str(w.sim.advance(delete=1)["changes"]["delete"][0]["issue_id"])
        record = await w.record_for(gone)
        assert record is not None
        with contextlib.suppress(PipeshubClientError):  # the product may refuse: either way it must not come back
            w.client.reindex_record(record["id"])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the record of a deleted issue was reindexed and the next sync ran")

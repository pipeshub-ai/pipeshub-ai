# pyright: ignore-file

"""
Jira Data Center on the simulator – what must never happen
==========================================================

  TC-JDSIM-SEC-001  A person with a PipesHub account and no Jira account reads nothing of the connector
  TC-JDSIM-SEC-002  A markup and a path in an issue's summary are stored as the text they are
  TC-JDSIM-SEC-003  No credential of the connector (token, password) is in any record, record group or permission name
                    it stored
  TC-JDSIM-SEC-004  An attachment download redirected to another host (switch attachment_redirect; an assumption, Data Center's
                    REST documentation does not describe a redirect): the credential is never sent to the other host; the
                    stream fails or carries the file's bytes
  The private-address policy beyond a loopback address is TC-JDSIM-AUTH-005.
"""

import pytest
import requests
from connectors.jira_data_center.sim_world import OUTSIDER_EMAIL, PASSWORD, JiraDcWorld, succeeded
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

HOSTILE = '<img src=x onerror=alert(1)> ../../etc/passwd "; DROP TABLE records; --'


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimSecurity:

    async def test_tc_jdsim_sec_001_no_jira_account_reads_nothing(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(prepare=[
            {"op": "grant", "project": "LIGH", "holder": "anyone"},
            {"op": "grant", "project": "LIGH", "holder": "applicationRole", "parameter": ""}]))
        await w.assert_matches_source("after the first sync")
        held = await w.holdings()
        assert held[OUTSIDER_EMAIL] == set(), "a PipesHub user with no Jira account reads records of the connector"

    async def test_tc_jdsim_sec_002_hostile_summary_is_data(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        key = w.issue_keys("EVER")[0]
        assert succeeded(await w.start(reset=False, prepare=[{"op": "issue", "issue": key, "summary": HOSTILE}]))
        tid = next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == key)
        record = await w.record_for(tid)
        assert record is not None and HOSTILE in str(record["recordName"]), record
        await w.assert_matches_source("after an issue was given a hostile summary")

    async def test_tc_jdsim_sec_003_no_credential_in_what_is_stored(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(auth="basic"))
        rows = await w.graph.fetch_records_by_type(w.connector_id, "")
        blob = str(rows)
        assert PASSWORD not in blob and "pat-svc-pipeshub-sync" not in blob
        shown = str(w.client.get_connector(w.connector_id))
        assert PASSWORD not in shown, "the connector's password is returned by the API that describes it"

    async def test_tc_jdsim_sec_004_redirected_download_leaks_no_credential(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        for auth in ("token", "basic"):
            assert succeeded(await w.start(auth=auth))
            by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
            fid = next(f for t in sorted(w.sim.expected()["ticket_index"].values(), key=lambda t: t["key"])
                       for f in t["files"] if f"attachment_{f}" in by_ext)
            name = w.sim.vendor_get(f"/rest/api/2/attachment/{fid}").json()["filename"]
            want = w.sim.vendor_get(f"/secure/attachment/{fid}/{name}").content
            w.sim.call("DELETE", "/_sim/jiradc/redirects")
            w.sim.switches(attachment_redirect="http://jira-dc-sim-files.internal")
            try:
                got: bytes | None = w.client.stream_record(by_ext[f"attachment_{fid}"]["id"]).content
            except (requests.RequestException, PipeshubClientError):
                got = None
            asked = w.sim.call("GET", "/_sim/jiradc/redirects")
            w.sim.call("DELETE", "/_sim/jiradc/switches")
            assert got is None or got == want, f"{auth}: a redirected download streamed {len(got or b'')} bytes that are not the file"
            assert all(not x["authorization"] for x in asked), f"{auth}: the credential went to the other host: {asked}"
            w.disable()
            await w._wait_idle()
            w.remove_connector()

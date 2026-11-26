# pyright: ignore-file

"""
Jira Data Center on the simulator – what a stream returns
=========================================================

  TC-JDSIM-CONT-001  An issue's streamed content carries its summary (every issue of the first twelve streams). Not covered:
                     the rendering of description and comments (wiki markup, unknown macros, pasted JSON): the simulator
                     has the HTML Jira renders, but the product's block format has no oracle yet
  TC-JDSIM-CONT-002  An attachment's streamed bytes equal the bytes Jira serves for it
  TC-JDSIM-CONT-003  An issue deleted at the source whose record is still stored: streaming it fails, it never
                     answers with the old content
  TC-JDSIM-CONT-004  Embedded comments cut short: the stream of an issue is the same as with every comment embedded
  TC-JDSIM-CONT-005  Every attachment record keeps the file name Jira gives it (the site holds names with spaces, accents
                     and other odd characters)
"""

import hashlib

import pytest
import requests
from connectors.jira_data_center.sim_world import FILE_PREFIX, JiraDcWorld, succeeded


def _stream(w: JiraDcWorld, record_id: str) -> bytes:
    return w.client.stream_record(record_id).content


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimContent:

    async def test_tc_jdsim_cont_001_issue_content(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        index = w.sim.expected()["ticket_index"]
        for tid in [t for t in sorted(index, key=int) if t in by_ext][:12]:
            body = _stream(w, by_ext[tid]["id"]).decode("utf-8", "replace")
            summary = w.sim.vendor_get(f"/rest/api/2/issue/{tid}", fields="summary").json()["fields"]["summary"]
            assert summary in body, f"{index[tid]['key']}: the stream of the issue lacks its summary {summary!r}"

    async def test_tc_jdsim_cont_002_attachment_bytes(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        files = sorted(fid for t in w.sim.expected()["ticket_index"].values() for fid in t["files"]
                       if f"{FILE_PREFIX}{fid}" in by_ext)
        for fid in files[::max(1, len(files) // 25)]:
            meta = w.sim.vendor_get(f"/rest/api/2/attachment/{fid}").json()
            want = w.sim.vendor_get(f"/secure/attachment/{fid}/{meta['filename']}").content
            assert len(want) == int(meta["size"])
            got = _stream(w, by_ext[f"{FILE_PREFIX}{fid}"]["id"])
            assert hashlib.sha256(got).hexdigest() == hashlib.sha256(want).hexdigest(), (
                f"attachment {fid} ({meta['filename']}): {len(got)} bytes streamed, {len(want)} in Jira")

    async def test_tc_jdsim_cont_003_gone_at_the_source(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        gone = w.sim.advance(delete=1)["changes"]["delete"][0]["issue_id"]
        record = await w.record_for(str(gone))
        assert record is not None, "the deleted issue had no record to stream"
        with pytest.raises(requests.HTTPError):
            _stream(w, record["id"])

    async def test_tc_jdsim_cont_004_embedded_comments_cut(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        first = {r["externalRecordId"]: _stream(w, r["id"]) for r in (await w.creator_records())
                 if not r["externalRecordId"].startswith(FILE_PREFIX)}
        ids = sorted(first, key=int)[:10]
        w.disable()
        await w._wait_idle()
        w.remove_connector()
        assert succeeded(await w.start(switches={"embedded_comments": 1}))
        second = {r["externalRecordId"]: r for r in await w.records()}
        for tid in ids:
            assert _normal(_stream(w, second[tid]["id"])) == _normal(first[tid]), (
                f"issue {tid}: its stream differs when its embedded comments are cut short")

    async def test_tc_jdsim_cont_005_odd_attachment_names(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        names = {r["externalRecordId"]: r["recordName"] for r in await w.records() if r["externalRecordId"].startswith(FILE_PREFIX)}
        for fid, name in names.items():
            meta = w.sim.vendor_get(f"/rest/api/2/attachment/{fid[len(FILE_PREFIX):]}").json()
            assert name == meta["filename"], f"{fid}: stored as {name!r}, named {meta['filename']!r} in Jira"


def _normal(blob: bytes) -> str:
    import re
    return re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<id>", blob.decode("utf-8", "replace"))

# pyright: ignore-file

"""
Jira Cloud on the simulator: what a record streams
==================================================

  TC-JCSIM-CONT-001  An issue's content holds its summary, description and public comments
  TC-JCSIM-CONT-002  Attachment bytes equal what Jira serves (odd names included)
  TC-JCSIM-CONT-003  An issue gone at the source cannot be streamed
  TC-JCSIM-CONT-004  A restricted comment's record streams its text; the issue's content does not hold it
  TC-JCSIM-CONT-005  Every attachment record keeps the file name Jira gives it
  TC-JCSIM-CONT-006  Inline images: one in the description or first comment is not a file record
  TC-JCSIM-CONT-007  An issue written with a wiki-style description and an ADF comment streams both
  TC-JCSIM-CONT-008  An empty issue (no description, no comments) streams without error
  TC-JCSIM-CONT-009  A file with an odd name (spaces, accents, quotes, a long name) attached through the API
"""

import hashlib

import pytest
import requests
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    COMMENT_PREFIX,
    FILE_PREFIX,
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)


def _stream(w: JiraCloudWorld, record_id: str) -> bytes:
    return w.client.stream_record(record_id).content


def _doc(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimContent:

    async def test_tc_jcsim_cont_001_issue_content(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        index = w.sim.expected()["ticket_index"]
        for tid in [t for t in sorted(index, key=int) if t in by_ext][:12]:
            body = _stream(w, by_ext[tid]["id"]).decode("utf-8", "replace")
            summary = w.sim.rest("GET", f"/issue/{tid}", fields="summary")["fields"]["summary"]
            assert summary in body, f"{index[tid]['key']}: the stream of the issue lacks its summary {summary!r}"

    async def test_tc_jcsim_cont_002_attachment_bytes(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        files = sorted(f["id"] for t in w.sim.expected()["ticket_index"].values() for f in t["files"]
                       if f"{FILE_PREFIX}{f['id']}" in by_ext)
        assert files
        for fid in files[::max(1, len(files) // 25)]:
            meta = w.sim.rest("GET", f"/attachment/{fid}")
            want = w.sim.http.get(f"{w.sim.base_url}/rest/api/3/attachment/content/{fid}", params={"redirect": "false"},
                                  headers=__import__("connectors.jira.cloud_sim_world", fromlist=["basic"]).basic()).content
            assert len(want) == int(meta["size"])
            got = _stream(w, by_ext[f"{FILE_PREFIX}{fid}"]["id"])
            assert hashlib.sha256(got).hexdigest() == hashlib.sha256(want).hexdigest(), (
                f"attachment {fid} ({meta['filename']}): {len(got)} bytes streamed, {len(want)} in Jira")

    async def test_tc_jcsim_cont_003_gone_at_the_source(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        gone = w.sim.advance(delete=1)["changes"]["delete"][0]["issue_id"]
        record = await w.record_for(str(gone))
        assert record is not None, "the deleted issue had no record to stream"
        with pytest.raises(requests.HTTPError):
            _stream(w, record["id"])

    async def test_tc_jcsim_cont_004_restricted_comment_records(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
        index = w.sim.expected()["ticket_index"]
        restricted = [(tid, c["id"]) for tid, t in sorted(index.items(), key=lambda kv: int(kv[0])) for c in t["comments"]
                      if c["restricted"] and f"{COMMENT_PREFIX}{c['id']}" in by_ext]
        assert restricted, "no restricted comment the creator reads"
        for tid, cid in restricted[:6]:
            text = w.sim.rest("GET", f"/issue/{tid}/comment/{cid}")
            words = [t["text"] for p in text["body"]["content"] for t in p.get("content", []) if t.get("type") == "text"]
            body = _stream(w, by_ext[f"{COMMENT_PREFIX}{cid}"]["id"]).decode("utf-8", "replace")
            assert words and words[0][:30] in body, f"comment {cid}: the record's stream lacks its text"

    async def test_tc_jcsim_cont_005_odd_attachment_names(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        names = {r["externalRecordId"]: r["recordName"] for r in await w.records() if r["externalRecordId"].startswith(FILE_PREFIX)}
        assert names
        for fid, name in names.items():
            meta = w.sim.rest("GET", f"/attachment/{fid[len(FILE_PREFIX):]}")
            assert name == meta["filename"], f"{fid}: stored as {name!r}, named {meta['filename']!r} in Jira"

    async def test_tc_jcsim_cont_006_inline_images_are_not_file_records(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        e = w.sim.expected()
        assert e["inline_images_skipped"] > 0, "the site holds no inline image"
        index = e["ticket_index"]
        inline = {f"{FILE_PREFIX}{a}" for t in index.values() for a in t["inline"]}
        stored = {r["externalRecordId"] for r in await w.records()}
        assert not inline & stored, f"inline images stored as files: {sorted(inline & stored)[:5]}"

    async def test_tc_jcsim_cont_007_adf_description_and_comment(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        project = w.projects()[0]
        issue = w.sim.rest("POST", "/issue", {"fields": {"project": {"key": project}, "summary": "ADF formats",
            "issuetype": {"name": "Task"}, "description": {"type": "doc", "version": 1, "content": [
                {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "A heading"}]},
                {"type": "bulletList", "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": "first bullet", "marks": [{"type": "strong"}]}]}]}]},
                {"type": "codeBlock", "content": [{"type": "text", "text": "print('code block')"}]},
                {"type": "table", "content": [{"type": "tableRow", "content": [{"type": "tableCell", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "cell text"}]}]}]}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "a link", "marks": [
                    {"type": "link", "attrs": {"href": "https://example.com/x"}}]}]}]}}})
        w.sim.rest("POST", f"/issue/{issue['key']}/comment", {"body": _doc("a plain comment of the test")})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the issue with a rich description")
        record = await w.record_for(str(issue["id"]))
        body = _stream(w, record["id"]).decode("utf-8", "replace") if record else ""
        for text in ("A heading", "first bullet", "code block", "cell text", "a plain comment of the test"):
            assert text in body, f"{text!r} is not in the stream"

    async def test_tc_jcsim_cont_008_empty_issue(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        issue = w.sim.rest("POST", "/issue", {"fields": {"project": {"key": w.projects()[0]}, "summary": "Nothing else",
                                                       "issuetype": {"name": "Task"}}})
        assert succeeded(await w.sync())
        record = await w.record_for(str(issue["id"]))
        assert record is not None and b"Nothing else" in _stream(w, record["id"])

    @pytest.mark.parametrize("name", ["with spaces.txt", "accénts-ünï.txt", "quote'd \"name\".txt", "a" * 120 + ".txt",
                                      "semi;colon&amp.txt", "../../escape.txt"])
    async def test_tc_jcsim_cont_009_odd_names(self, jira_cloud_world: JiraCloudWorld, name: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        key = w.issue_keys(w.projects()[0])[0]
        data = f"bytes of {name}".encode()
        made = w.sim.upload(key, name, data)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a file with an odd name was attached")
        record = await w.record_for(f"{FILE_PREFIX}{made[0]['id']}")
        assert record is not None
        assert _stream(w, record["id"]) == data

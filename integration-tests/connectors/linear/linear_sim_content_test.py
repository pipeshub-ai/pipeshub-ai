# pyright: ignore-file

"""
Linear on the simulator: what is streamed, and what is not a file
=================================================================

Preview and indexing read a record's bytes through PipesHub's stream route, which asks Linear again (the issue, the project,
the document) or the file host. They must equal the source.

  TC-LNSIM-CNT-001  Every file linked from an issue, comment or project streams the source's bytes: a PDF, a text file, a name
                    with spaces, brackets and non-ASCII letters, and an empty file [J]
  TC-LNSIM-CNT-002  An issue streams its title, description, every comment (threads included), and nothing of another issue [J]
  TC-LNSIM-CNT-003  A project streams its text, update, milestones, comments [J]
  TC-LNSIM-CNT-004  A document streams its content, and an edited one its new content on the next read [J]
  TC-LNSIM-CNT-005  A record whose issue was deleted at the source cannot be streamed, and the next sync removes it [J, F]
  TC-LNSIM-CNT-006  Only links to Linear's own upload host become files: a look-alike host, a path that names the host and
                    another scheme are text, and no request left for them [J, N]
  TC-LNSIM-CNT-007  Streaming changes nothing: the comparison still holds afterwards [J]
  TC-LNSIM-CNT-008  The file host refusing the credential (the key revoked after the sync): streaming a file fails and the
                    record stays [J, A]
"""

import pytest
from connectors.linear.sim_instances import API_KEY, small, workspace
from connectors.linear.sim_world import LinearWorld, file_record_id, succeeded


def file_ids(w: LinearWorld) -> list[tuple[str, str, str]]:
    """(upload key, record id, upload name) for each file record the sync must hold."""
    x = w.sim.expected()
    out = []
    for upload in x["uploads"]:
        for ref in upload["linked_from"]:
            if ref["image"] or ref["where"] == "document":
                continue
            owners = x["issues"] if ref["kind"] == "issue" else x["projects"]
            owner = next(o for o in owners if o["key"] == ref["key"])
            out.append((upload["key"], file_record_id(upload["url"], owner["id"]), upload["key"]))
    return out


@pytest.mark.integration
@pytest.mark.linear_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLinearSimContent:

    async def test_tc_lnsim_cnt_001_files_stream_the_source_bytes(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        files = file_ids(w)
        assert {k for k, _, _ in files} >= {"spec", "notes", "odd"}, files
        for key, ext, _ in files:
            assert await w.stream(ext) == w.upload_bytes(key), f"file {key} did not stream the source's bytes"

    async def test_tc_lnsim_cnt_002_an_issue_streams_its_comments(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        text = (await w.stream(w.ident("issues", "eng-1"))).decode("utf-8", "replace")
        for needle in ("Ship the importer", "Started. Notes", "Reply in thread", "Second thread"):
            assert needle in text, f"the issue stream lacks {needle!r}"
        assert "keys rotated" not in text and "Patch window" not in text, "the stream carries another issue's text"

    async def test_tc_lnsim_cnt_003_a_project_streams_its_updates(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        text = (await w.stream(w.ident("projects", "p1"))).decode("utf-8", "replace")
        for needle in ("Launch", "On track", "Beta", "GA", "Nice", "Project comment"):
            assert needle in text, f"the project stream lacks {needle!r}"

    async def test_tc_lnsim_cnt_004_a_document_streams_its_content(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        ext = w.ident("documents", "d1")
        assert b"Design" in await w.stream(ext)
        w.ops({"op": "edit_document", "document": "d1", "content": "# Rewritten\n\nNEW-DOCUMENT-BODY"})
        assert b"NEW-DOCUMENT-BODY" in await w.stream(ext), "the stream served the old content"

    async def test_tc_lnsim_cnt_005_deleted_at_the_source(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        ext = w.ident("issues", "eng-2")
        w.ops({"op": "delete_issue", "issue": "eng-2"})
        with pytest.raises(Exception):  # noqa: B017, PT011 - the HTTP error of the stream
            await w.stream(ext)
        assert succeeded(await w.sync())
        assert await w.record_for(ext) is None, "the record of a deleted issue stayed"
        await w.assert_matches_source("after the issue was deleted")

    async def test_tc_lnsim_cnt_006_only_the_upload_host_makes_files(self, linear_world: LinearWorld) -> None:
        w = linear_world
        account = small()
        account["issues"][1]["description"] = (
            "[a](https://uploads.linear.app.evil.example/x/a.pdf) [b](https://evil.example/uploads.linear.app/b.pdf) "
            "[c](http://uploads.linear.app/x/c.pdf) [d](https://uploads.linear.app@evil.example/d.pdf) [ok]({{upload:spec}})")
        assert succeeded(await w.start(account))
        files = [r for r in await w.records() if r["recordType"] == "FILE"]
        urls = [r["externalRecordId"].split("#parent=")[0] for r in files]
        assert all(u.startswith("https://uploads.linear.app/") for u in urls), urls
        assert any(u.endswith("spec.pdf") for u in urls), "the real upload was not a file"
        await w.assert_matches_source("a description with look-alike links")

    async def test_tc_lnsim_cnt_007_streaming_changes_nothing(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        for _, ext, _ in file_ids(w):
            await w.stream(ext)
        await w.stream(w.ident("issues", "eng-1"))
        await w.assert_matches_source("after streaming")

    async def test_tc_lnsim_cnt_008_file_host_refuses_the_credential(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small()))
        ext = file_ids(w)[0][1]
        w.sim.call("POST", "/_sim/linear/keys/revoke", {"key": API_KEY})
        with pytest.raises(Exception):  # noqa: B017, PT011 - the HTTP error of the stream
            await w.stream(ext)
        assert await w.record_for(ext), "a refused download removed the record"

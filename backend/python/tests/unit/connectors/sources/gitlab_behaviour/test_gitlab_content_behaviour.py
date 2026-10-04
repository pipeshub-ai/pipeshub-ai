"""What indexing reads back: issue and merge request content, attachments, reindex and pickers."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import pytest
from gitlab_server_fake import REPORTER
from gitlab_world import ALICE, WEB, blob, build_acme, web_mr_ids

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse

SHOT = "/uploads/" + "c" * 32 + "/shot.png"
SPEC = "/uploads/" + "d" * 32 + "/spec.pdf"
NOTE_IMG = "/uploads/" + "e" * 32 + "/note.png"
PNG = b"\x89PNG\r\n\x1a\nfake-image"
WHEN = {"created_at": "2026-09-01T11:00:00Z", "updated_at": "2026-09-01T11:00:00Z"}


async def body_of(response: StreamingResponse) -> bytes:
    return b"".join([chunk if isinstance(chunk, bytes) else chunk.encode() async for chunk in response.body_iterator])


async def test_an_issue_streams_with_its_description_images_comments_and_attachments(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.uploads[SHOT] = PNG
    gitlab.uploads[NOTE_IMG] = PNG + b"2"
    gitlab.add_issue(WEB, 1, "Login broken", "2026-09-01T10:00:00Z", author=ALICE,
                     description=f"Steps below\n\n![shot]({SHOT})\n\n[spec]({SPEC})",
                     notes=[{"id": 1, "body": f"Seen it ![n]({NOTE_IMG})", "author": {"username": "alice"}}])
    connector = await harness.sync()

    blocks = json.loads(await body_of(await connector.stream_record(db.records["11001"])))

    description, comment = blocks["block_groups"][0], blocks["block_groups"][1]
    assert description["data"].startswith("# Login broken")
    assert base64.b64encode(PNG).decode() in description["data"]
    spec_record = db.records[f"https://gitlab.example.com/api/v4/projects/11{SPEC}"]
    assert [c["child_id"] for c in description["children_records"]] == [spec_record.id]
    assert comment["name"] == "Comment by alice on issue 1"
    assert base64.b64encode(PNG + b"2").decode() in comment["data"]


async def test_an_attachment_streams_its_bytes(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.uploads[SPEC] = b"%PDF-1.7 spec"
    gitlab.add_issue(WEB, 1, "Spec", "2026-09-01T10:00:00Z", description=f"[spec]({SPEC})")
    connector = await harness.sync()

    record = db.records[f"https://gitlab.example.com/api/v4/projects/11{SPEC}"]
    assert await body_of(await connector.stream_record(record)) == b"%PDF-1.7 spec"


async def test_a_merge_request_streams_comments_file_changes_and_commits(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.add_merge_request(
        WEB, 1, "Fix login", "2026-09-01T11:00:00Z", description="Fixes the login",
        notes=[
            {"id": 1, "body": "Please look", "author": {"username": "bob"}, "system": False, **WHEN},
            {"id": 2, "body": "assigned to @alice", "author": {"username": "alice"}, "system": True, **WHEN},
            {"id": 3, "body": "Rename this", "author": {"username": "alice"}, "system": False,
             "position": {"new_path": "src/app.py"}, **WHEN},
        ],
        changes=[{"new_path": "src/app.py", "old_path": "src/app.py", "diff": "@@ -1 +1 @@",
                  "new_file": False, "deleted_file": False}],
        commits=[{"id": "abc123", "title": "Fix", "message": "Fix login bug",
                  "web_url": "https://gitlab.example.com/acme/web/-/commit/abc123",
                  "committed_date": "2026-09-01T10:30:00Z"}],
    )
    connector = await harness.sync()

    mr = db.records[next(iter(web_mr_ids(1)))]
    blocks = json.loads(await body_of(await connector.stream_record(mr)))

    groups = blocks["block_groups"]
    names = [g["name"] for g in groups]
    assert "Comment by bob on merge request 1" in names
    assert "System Comment by alice on merge request 1" in names
    change = next(g for g in groups if g["name"] == "block for file src/app.py")
    assert "print('app')" in change["data"]
    assert change["comments"][0][0]["text"] == "Rename this"
    assert [b["data"] for b in blocks["blocks"]] == ["Fix login bug"]


async def test_reindex_refreshes_changed_issues_and_requeues_everything_else(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Old title", "2026-09-01T10:00:00Z")
    gitlab.add_merge_request(WEB, 1, "MR", "2026-09-01T11:00:00Z")
    connector = await harness.sync()
    gitlab.add_issue(WEB, 1, "New title", "2026-09-06T10:00:00Z")
    gitlab.add_merge_request(WEB, 1, "MR, reworded", "2026-09-07T11:00:00Z")
    mr_id = next(iter(web_mr_ids(1)))

    await connector.reindex_records([db.records["11001"], db.records[mr_id], db.records[blob("README.md")]])

    assert db.records["11001"].record_name == "New title"
    assert db.records[mr_id].record_name == "MR, reworded"
    assert [r.external_record_id for r in db.reindexed] == [blob("README.md")]


async def test_the_group_and_project_pickers_list_what_the_token_can_see(harness, gitlab) -> None:
    build_acme(gitlab)
    connector = await harness.connector()

    groups = await connector.get_filter_options("group_ids")
    projects = await connector.get_filter_options("project_ids", search="web")
    too_short = await connector.get_filter_options("project_ids", search="we")

    assert [o.id for o in groups.options] == ["acme", "acme/platform"]
    assert [o.id for o in projects.options] == ["acme/web"]
    assert too_short.options == [] and "at least 3" in (too_short.message or "")
    with pytest.raises(ValueError):
        await connector.get_filter_options("labels")


async def test_a_failure_that_only_mentions_401_does_not_trigger_a_token_refresh(harness, gitlab, db, token_refresher) -> None:
    build_acme(gitlab)
    gitlab.add_project(1401, "acme/legacy", members={ALICE: REPORTER}, files={"a.py": "a\n"})
    gitlab.add_issue(1401, 1, "Broken image", "2026-09-01T10:00:00Z", description=f"![gone]({SHOT})")
    connector = await harness.sync()

    blocks = json.loads(await body_of(await connector.stream_record(db.records["1401001"])))

    assert blocks["block_groups"][0]["data"].startswith("# Broken image")
    assert token_refresher.calls == []


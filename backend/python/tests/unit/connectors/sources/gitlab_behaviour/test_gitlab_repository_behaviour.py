"""Code repositories: full walks, incremental compares, deletions, renames and moves."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from gitlab_server_fake import blob_sha, parse_time
from gitlab_world import API, WEB, blob, build_acme, tree

from app.config.constants.arangodb import MimeTypes

if TYPE_CHECKING:
    from gitlab_store_fakes import FakeRecordsDb

WEB_CODE = {blob("README.md"), blob("src/app.py"), blob("src/util/helpers.py")}


def ms(ts: str) -> int:
    return int(parse_time(ts).timestamp() * 1000)


def folders(db: FakeRecordsDb) -> set[str]:
    return {ext for ext, r in db.by_type("FILE").items() if r.mime_type == MimeTypes.FOLDER.value}


def web_code(db: FakeRecordsDb) -> set[str]:
    return {ext for ext in db.by_type("CODE_FILE") if ext.startswith("/acme/web")}


async def test_a_full_walk_follows_every_tree_page_and_skips_ignored_files(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    gitlab.graphql_page_size = 2

    await harness.sync()

    assert web_code(db) == WEB_CODE
    assert {tree("src"), tree("src/util")} <= folders(db)
    cursors = [r.body for r in gitlab.calls("POST", r"^/api/graphql$") if b'"acme/web"' in r.body]
    assert len(cursors) == 5
    assert checkpoints.code_checkpoint(WEB) == gitlab.projects[WEB].head.sha
    helpers = db.records[blob("src/util/helpers.py")]
    assert helpers.parent_external_record_id == tree("src/util")
    assert helpers.external_revision_id == blob_sha(b"def helper():\n    return 1\n")


async def test_an_incremental_sync_applies_edits_additions_and_deletions(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    await harness.sync()
    app_id = db.records[blob("src/app.py")].id
    walks_before = len(gitlab.calls("POST", r"^/api/graphql$"))

    head = gitlab.change_files(WEB, write={"src/app.py": "print('v2')\n", "docs/guide.md": "# guide\n"},
                               delete=("src/util/helpers.py",))
    await harness.sync()

    assert web_code(db) == {blob("README.md"), blob("src/app.py"), blob("docs/guide.md")}
    app = db.records[blob("src/app.py")]
    assert app.id == app_id
    assert app.external_revision_id == blob_sha(b"print('v2')\n")
    assert tree("docs") in folders(db)
    assert tree("src/util") not in folders(db)
    assert tree("src") in folders(db)
    assert gitlab.calls("GET", rf"^/api/v4/projects/{WEB}/repository/compare$")
    assert len(gitlab.calls("POST", r"^/api/graphql$")) == walks_before
    assert checkpoints.code_checkpoint(WEB) == head


async def test_a_renamed_file_keeps_its_record(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()
    readme_id = db.records[blob("README.md")].id

    gitlab.change_files(WEB, rename={"README.md": "docs/README.md"})
    await harness.sync()

    assert blob("README.md") not in db.records
    assert db.records[blob("docs/README.md")].id == readme_id
    assert db.records[blob("docs/README.md")].parent_external_record_id == tree("docs")


async def test_a_move_reported_as_delete_plus_add_is_still_recognised_by_content(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.projects[WEB].report_renames = False
    await harness.sync()
    helpers_id = db.records[blob("src/util/helpers.py")].id

    gitlab.change_files(WEB, rename={"src/util/helpers.py": "lib/helpers.py"})
    await harness.sync()

    assert db.records[blob("lib/helpers.py")].id == helpers_id
    assert blob("src/util/helpers.py") not in db.records
    assert tree("src/util") not in folders(db)


async def test_a_tree_page_that_fails_once_is_retried_with_backoff(harness, gitlab, db, checkpoints, repo_backoff) -> None:
    build_acme(gitlab)
    gitlab.graphql_page_size = 2
    gitlab.fail("POST", r"^/api/graphql$", 502, skip=1, times=1, body_contains='"acme/web"')

    await harness.sync()

    assert repo_backoff == [2.0]
    assert web_code(db) == WEB_CODE
    assert checkpoints.code_checkpoint(WEB) == gitlab.projects[WEB].head.sha


async def test_a_tree_walk_that_keeps_failing_withholds_the_checkpoint_until_it_completes(harness, gitlab, db, checkpoints,
                                                                                         repo_backoff) -> None:
    build_acme(gitlab)
    gitlab.graphql_page_size = 2
    gitlab.fail("POST", r"^/api/graphql$", 502, skip=1, body_contains='"acme/web"')

    await harness.sync()
    assert repo_backoff == [2.0, 4.0]
    assert checkpoints.code_checkpoint(WEB) is None

    gitlab.clear_faults()
    await harness.sync()
    assert web_code(db) == WEB_CODE
    assert checkpoints.code_checkpoint(WEB) == gitlab.projects[WEB].head.sha


async def test_a_tree_walk_whose_cursor_stops_moving_is_abandoned_without_a_checkpoint(harness, gitlab, db,
                                                                                    checkpoints) -> None:
    build_acme(gitlab)
    gitlab.graphql_page_size = 2
    stuck = {"data": {"project": {"repository": {"paginatedTree": {
        "nodes": [{"trees": {"nodes": []}, "blobs": {"nodes": []}}],
        "pageInfo": {"endCursor": "2", "hasNextPage": True},
    }}}}}
    gitlab.fail("POST", r"^/api/graphql$", 200, body=stuck,
                body_contains='"fullPath":"acme/web","branch":"HEAD","afterCursor":"2"')

    await harness.sync()

    assert checkpoints.code_checkpoint(WEB) is None
    assert checkpoints.code_checkpoint(API) is not None


async def test_a_truncated_tree_page_is_retried(harness, gitlab, db, checkpoints, repo_backoff) -> None:
    build_acme(gitlab)
    gitlab.fail("POST", r"^/api/graphql$", 200, times=1, raw=b'{"data": {"proj', body_contains='"acme/web"')

    await harness.sync()

    assert repo_backoff == [2.0]
    assert web_code(db) == WEB_CODE


async def test_too_many_changes_for_a_compare_falls_back_to_a_full_walk(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    await harness.sync()
    gitlab.projects[WEB].compare_overflow = True

    head = gitlab.change_files(WEB, write={"src/app.py": "print('v3')\n"})
    await harness.sync()

    assert db.records[blob("src/app.py")].external_revision_id == blob_sha(b"print('v3')\n")
    assert checkpoints.code_checkpoint(WEB) == head


async def test_an_archived_project_keeps_syncing(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.projects[WEB].archived = True
    gitlab.add_issue(WEB, 1, "Old ticket", "2026-09-01T10:00:00Z")

    await harness.sync()

    assert web_code(db) == WEB_CODE
    assert "11001" in db.records
    assert "alice@example.com" in db.access(blob("README.md"))


async def test_file_dates_are_filled_in_from_commit_history_after_the_sync(harness, gitlab, db) -> None:
    build_acme(gitlab)

    await harness.sync()

    readme = db.records[blob("README.md")]
    assert readme.source_created_at == ms("2026-09-01T10:00:00Z")
    assert readme.source_updated_at == ms("2026-09-01T10:00:00Z")


async def test_streaming_a_code_file_returns_its_bytes(harness, gitlab, db) -> None:
    build_acme(gitlab)
    connector = await harness.sync()

    response = await connector.stream_record(db.records[blob("src/app.py")])
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert body == b"print('app')\n"


@pytest.mark.xfail(strict=True, reason=(
    "repos.py falls back to a full walk when the compare cannot be used (history rewritten, too many "
    "changes), and a full walk only adds and updates, so files deleted in that range are never removed"
))
async def test_files_deleted_across_a_force_push_are_removed(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()

    project = gitlab.projects[WEB]
    files = dict(project.head.files)
    files.pop("src/util/helpers.py")
    project.commits = []
    gitlab.commit(WEB, files, when="2026-09-03T10:00:00Z")
    await harness.sync()

    assert blob("src/util/helpers.py") not in db.records


@pytest.mark.xfail(strict=True, reason=(
    "repos.py keys code files by the project path, so after a project is renamed or moved the next "
    "change creates a second record for the same file and deletions no longer find the old one"
))
async def test_renaming_a_project_does_not_duplicate_its_files(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()

    gitlab.projects[WEB].path_with_namespace = "acme/website"
    gitlab.change_files(WEB, write={"src/app.py": "print('v2')\n"}, delete=("README.md",))
    await harness.sync()

    code = {ext for ext in db.by_type("CODE_FILE") if "/-/blob/HEAD/" in ext and not ext.startswith("/acme/platform")}
    assert {ext.split("/-/blob/HEAD/")[1] for ext in code} == {"src/app.py", "src/util/helpers.py"}
    assert len(code) == 2


@pytest.mark.xfail(strict=True, reason=(
    "repos.py retries a rate-limited tree page after a fixed 2s/4s backoff and ignores Retry-After, so "
    "the retries land inside the limit window and a large walk is abandoned"
))
async def test_a_rate_limited_tree_page_waits_as_long_as_gitlab_asks(harness, gitlab, db, repo_backoff) -> None:
    build_acme(gitlab)
    gitlab.fail("POST", r"^/api/graphql$", 429, times=1, headers={"Retry-After": "30"})

    await harness.sync()

    assert repo_backoff and max(repo_backoff) >= 30


@pytest.mark.xfail(strict=True, reason=(
    "repos.py writes changed files without dates and the backfill only fills records that have none, "
    "so a file's last-modified date stays at the first sync's commit forever"
))
async def test_a_changed_file_gets_its_new_last_modified_date(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()

    gitlab.change_files(WEB, write={"src/app.py": "print('v2')\n"}, when="2026-09-05T10:00:00Z")
    await harness.sync()

    assert db.records[blob("src/app.py")].source_updated_at == ms("2026-09-05T10:00:00Z")

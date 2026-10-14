"""Issues and merge requests: paging, incremental checkpoints, and partial failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gitlab_server_fake import FakeGitLab, SeenRequest, parse_time
from gitlab_world import ALICE, BOB, WEB, build_acme, web_issue_ids, web_mr_ids

from app.config.constants.arangodb import ProgressStatus

if TYPE_CHECKING:
    from gitlab_store_fakes import FakeRecordsDb


def ms(ts: str) -> int:
    return int(parse_time(ts).timestamp() * 1000)


def issue_listings(gitlab: FakeGitLab) -> list[SeenRequest]:
    return gitlab.calls("GET", r"^/api/v4/projects/11/issues$")


def written_since(db: FakeRecordsDb, mark: int) -> set[str]:
    return {ext for batch in db.record_writes[mark:] for ext in batch}


async def test_every_issue_and_merge_request_is_read_across_all_pages(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    gitlab.max_per_page = 2
    for iid in range(1, 6):
        gitlab.add_issue(WEB, iid, f"Issue {iid}", f"2026-09-0{iid}T10:00:00Z")
    for iid in range(1, 4):
        gitlab.add_merge_request(WEB, iid, f"MR {iid}", f"2026-09-0{iid}T11:00:00Z")

    await harness.sync()

    assert set(db.by_type("TICKET")) == web_issue_ids(1, 2, 3, 4, 5)
    assert set(db.by_type("PULL_REQUEST")) == web_mr_ids(1, 2, 3)
    assert [r.params.get("page", "1") for r in issue_listings(gitlab)] == ["1", "2", "3"]
    assert checkpoints.issues_checkpoint(WEB) == ms("2026-09-05T10:00:00Z")
    assert checkpoints.mrs_checkpoint(WEB) == ms("2026-09-03T11:00:00Z")


async def test_second_sync_reads_only_changes_since_the_checkpoint_without_duplicates(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    for iid in range(1, 4):
        gitlab.add_issue(WEB, iid, f"Issue {iid}", f"2026-09-0{iid}T10:00:00Z")
    await harness.sync()
    ids_before = {ext: r.id for ext, r in db.by_type("TICKET").items()}

    gitlab.add_issue(WEB, 2, "Issue 2, retitled", "2026-09-10T10:00:00Z")
    mark = len(db.record_writes)
    await harness.sync()

    since = parse_time(issue_listings(gitlab)[-1].params["updated_after"])
    assert int(since.timestamp() * 1000) == ms("2026-09-03T10:00:00Z")
    assert "11002" in written_since(db, mark)
    assert "11001" not in written_since(db, mark)
    assert {ext: r.id for ext, r in db.by_type("TICKET").items()} == ids_before
    assert db.records["11002"].record_name == "Issue 2, retitled"
    assert checkpoints.issues_checkpoint(WEB) == ms("2026-09-10T10:00:00Z")


async def test_a_failed_write_keeps_the_checkpoint_and_the_next_sync_catches_up(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    for iid in range(1, 8):
        gitlab.add_issue(WEB, iid, f"Issue {iid}", f"2026-09-0{iid}T10:00:00Z")
    db.fail_writes_for = {"11006"}

    await harness.sync()
    assert set(db.by_type("TICKET")) == web_issue_ids(1, 2, 3, 4, 5)
    assert checkpoints.issues_checkpoint(WEB) is None

    await harness.sync()
    assert set(db.by_type("TICKET")) == web_issue_ids(*range(1, 8))
    assert checkpoints.issues_checkpoint(WEB) == ms("2026-09-07T10:00:00Z")


async def test_a_listing_page_that_fails_never_advances_the_checkpoint(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    gitlab.max_per_page = 2
    for iid in range(1, 6):
        gitlab.add_issue(WEB, iid, f"Issue {iid}", f"2026-09-0{iid}T10:00:00Z")
    gitlab.fail("GET", r"^/api/v4/projects/11/issues$", 403, skip=1)

    await harness.sync()
    assert db.by_type("TICKET") == {}
    assert checkpoints.issues_checkpoint(WEB) is None

    gitlab.clear_faults()
    await harness.sync()
    assert set(db.by_type("TICKET")) == web_issue_ids(1, 2, 3, 4, 5)


async def test_confidential_issues_are_hidden_from_guests_unless_they_wrote_or_own_them(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Open plan", "2026-09-01T10:00:00Z", author=ALICE)
    gitlab.add_issue(WEB, 2, "Salary review", "2026-09-02T10:00:00Z", confidential=True, author=ALICE)
    gitlab.add_issue(WEB, 3, "Bob's report", "2026-09-03T10:00:00Z", confidential=True, author=BOB)
    gitlab.add_issue(WEB, 4, "Assigned to Bob", "2026-09-04T10:00:00Z", confidential=True, author=ALICE,
                     assignees=(BOB,))

    await harness.sync()

    assert "bob@example.com" in db.access("11001")
    assert "bob@example.com" not in db.access("11002")
    assert "bob@example.com" in db.access("11003")
    assert "bob@example.com" in db.access("11004")
    assert {"alice@example.com", "owner@example.com"} <= db.access("11002")


async def test_changing_confidentiality_moves_the_issue_between_audiences(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Was public", "2026-09-01T10:00:00Z", author=ALICE)
    gitlab.add_issue(WEB, 2, "Was secret", "2026-09-02T10:00:00Z", confidential=True, author=ALICE)
    await harness.sync()
    assert "bob@example.com" in db.access("11001")

    gitlab.add_issue(WEB, 1, "Now secret", "2026-09-05T10:00:00Z", confidential=True, author=ALICE)
    gitlab.add_issue(WEB, 2, "Now public", "2026-09-05T11:00:00Z", author=ALICE)
    await harness.sync()

    assert "bob@example.com" not in db.access("11001")
    assert "bob@example.com" in db.access("11002")


async def test_files_attached_to_a_confidential_issue_share_its_audience(harness, gitlab, db) -> None:
    build_acme(gitlab)
    spec = "/uploads/" + "a" * 32 + "/spec.pdf"
    notes_file = "/uploads/" + "b" * 32 + "/log.txt"
    gitlab.add_issue(WEB, 1, "Secret", "2026-09-01T10:00:00Z", confidential=True, author=ALICE,
                     description=f"See [spec]({spec})",
                     notes=[{"id": 1, "body": f"[log]({notes_file})", "author": {"username": "alice"}}])

    await harness.sync()

    files = db.by_type("FILE")
    spec_id = f"https://gitlab.example.com/api/v4/projects/11{spec}"
    log_id = f"https://gitlab.example.com/api/v4/projects/11{notes_file}"
    assert {spec_id, log_id} <= set(files)
    for ext in (spec_id, log_id):
        assert files[ext].parent_external_record_id == "11001"
        assert "bob@example.com" not in db.access(ext)
        assert "alice@example.com" in db.access(ext)


async def test_the_modified_date_filter_bounds_the_first_listing(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Old", "2026-06-01T10:00:00Z")
    gitlab.add_issue(WEB, 2, "Recent", "2026-09-02T10:00:00Z")
    harness.set_sync_filter("modified", "is_after", {"start": ms("2026-09-01T00:00:00Z"), "end": None}, "datetime")

    await harness.sync()

    since = parse_time(issue_listings(gitlab)[0].params["updated_after"])
    assert since == parse_time("2026-09-01T00:00:00Z")
    assert set(db.by_type("TICKET")) == web_issue_ids(2)


async def test_turning_off_issue_indexing_still_syncs_issues_but_does_not_index_them(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Issue", "2026-09-01T10:00:00Z")
    gitlab.add_merge_request(WEB, 1, "MR", "2026-09-01T11:00:00Z")
    harness.set_indexing_filter("issues", False)

    await harness.sync()

    assert db.records["11001"].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
    assert db.records[next(iter(web_mr_ids(1)))].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


async def test_an_issue_whose_comments_cannot_be_read_does_not_stop_the_others(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    for iid in range(1, 5):
        gitlab.add_issue(WEB, iid, f"Issue {iid}", f"2026-09-0{iid}T10:00:00Z")
    gitlab.fail("GET", r"^/api/v4/projects/11/issues/2/notes$", 403)

    await harness.sync()

    assert set(db.by_type("TICKET")) == web_issue_ids(1, 2, 3, 4)
    assert checkpoints.issues_checkpoint(WEB) < ms("2026-09-02T10:00:00Z")

    gitlab.clear_faults()
    await harness.sync()
    since = parse_time(issue_listings(gitlab)[-1].params["updated_after"])
    assert since <= parse_time("2026-09-02T10:00:00Z")
    assert checkpoints.issues_checkpoint(WEB) == ms("2026-09-04T10:00:00Z")


async def test_an_issue_that_fails_to_save_is_retried_rather_than_skipped_forever(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    for iid in range(1, 5):
        gitlab.add_issue(WEB, iid, f"Issue {iid}", f"2026-09-0{iid}T10:00:00Z")
    db.fail_lookup_for = {"11002"}

    await harness.sync()
    assert set(db.by_type("TICKET")) == web_issue_ids(1, 3, 4)

    db.fail_lookup_for = set()
    await harness.sync()
    assert set(db.by_type("TICKET")) == web_issue_ids(1, 2, 3, 4)
    assert checkpoints.issues_checkpoint(WEB) == ms("2026-09-04T10:00:00Z")


async def test_a_merge_request_whose_comments_cannot_be_read_does_not_stop_the_others(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    for iid in range(1, 4):
        gitlab.add_merge_request(WEB, iid, f"MR {iid}", f"2026-09-0{iid}T11:00:00Z")
    gitlab.fail("GET", r"^/api/v4/projects/11/merge_requests/1/notes$", 403)

    await harness.sync()

    assert set(db.by_type("PULL_REQUEST")) == web_mr_ids(1, 2, 3)
    assert checkpoints.mrs_checkpoint(WEB) < ms("2026-09-01T11:00:00Z")

    gitlab.clear_faults()
    await harness.sync()
    assert checkpoints.mrs_checkpoint(WEB) == ms("2026-09-03T11:00:00Z")


async def test_a_merge_request_that_fails_to_save_is_retried_on_the_next_sync(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    for iid in range(1, 4):
        gitlab.add_merge_request(WEB, iid, f"MR {iid}", f"2026-09-0{iid}T11:00:00Z")
    db.fail_lookup_for = set(web_mr_ids(2))

    await harness.sync()
    assert set(db.by_type("PULL_REQUEST")) == web_mr_ids(1, 3)

    db.fail_lookup_for = set()
    await harness.sync()
    assert set(db.by_type("PULL_REQUEST")) == web_mr_ids(1, 2, 3)

"""Sync progress: what a run counts, what it leaves behind, and how it is read back.

The numbers people see must be right or absent. A record reaches the processor
once per user who can see it, so counts are per record per run; a run's end
folds its live snapshot into history in the same write as IDLE; and a failure
keeps a reason someone can act on.
"""

import asyncio
import contextlib
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.constants import ConnectorStateKeys as Keys
from app.connectors.core.sync.progress_view import (
    STALL_AFTER_MS,
    _indexing_summary,
    _live_run,
    build_progress_view,
)
from app.connectors.core.sync.sync_errors import classify_sync_error
from app.connectors.core.sync.sync_progress import (
    SYNC_RUNS_KEPT,
    ChangeKind,
    SyncPhase,
    SyncProgress,
    current_sync_progress,
    merged_run_history,
    parse_run_history,
    reset_current_sync_progress,
    set_current_sync_progress,
)
from app.connectors.core.sync.sync_runner import record_failed_start, run_sync_task
from app.utils.time_conversion import get_epoch_timestamp_in_ms

_LOG = logging.getLogger("test")


def _progress() -> SyncProgress:
    return SyncProgress(connector_id="c1", run_id="r1", full=False)


# ---- counting -------------------------------------------------------------


def test_a_record_seen_by_many_users_counts_once() -> None:
    p = _progress()
    for _ in range(50):
        p.note_record("file-1", ChangeKind.NEW)
    assert p.counts()["found"] == 1
    assert p.new == 1


def test_a_record_first_seen_unchanged_then_changed_counts_as_changed() -> None:
    p = _progress()
    p.note_record("file-1", ChangeKind.UNCHANGED)
    p.note_record("file-1", ChangeKind.CHANGED)
    assert (p.unchanged, p.changed, p.counts()["found"]) == (0, 1, 1)


def test_a_record_already_counted_new_is_not_moved() -> None:
    p = _progress()
    p.note_record("file-1", ChangeKind.NEW)
    p.note_record("file-1", ChangeKind.UNCHANGED)
    assert (p.new, p.unchanged) == (1, 0)


def test_phase_only_moves_forward() -> None:
    p = _progress()
    p.enter_phase(SyncPhase.PEOPLE)
    p.note_containers(3)
    p.note_record("r", ChangeKind.NEW)
    assert p.phase is SyncPhase.CONTENT
    p.note_containers(1)
    assert p.phase is SyncPhase.CONTENT


def test_short_pauses_are_not_reported_as_throttling() -> None:
    p = _progress()
    p.note_throttled(2)
    assert p.snapshot()["throttledUntil"] is None
    p.note_throttled(120)
    assert p.snapshot()["throttledUntil"] > get_epoch_timestamp_in_ms()


def test_a_closed_run_ignores_late_reports() -> None:
    p = _progress()
    p.close()
    p.note_record("r", ChangeKind.NEW)
    p.note_removed(3)
    assert p.counts()["found"] == 0 and p.removed == 0


def test_progress_is_only_visible_to_its_own_connector() -> None:
    token = set_current_sync_progress(_progress())
    try:
        assert current_sync_progress("c1") is not None
        assert current_sync_progress("kb-1") is None
    finally:
        reset_current_sync_progress(token)
    assert current_sync_progress() is None


def test_history_is_newest_first_and_capped() -> None:
    stored = None
    for i in range(SYNC_RUNS_KEPT + 3):
        stored = merged_run_history(stored, {"runId": f"r{i}"})
    runs = parse_run_history(stored)
    assert len(runs) == SYNC_RUNS_KEPT
    assert runs[0]["runId"] == f"r{SYNC_RUNS_KEPT + 2}"


def test_unreadable_history_reads_as_empty() -> None:
    assert parse_run_history("not json") == []
    assert parse_run_history(None) == []


# ---- failure reasons ------------------------------------------------------


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com/x?token=abc")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status, request=request))


class AtlassianQuotaExhausted(Exception):
    pass


class ZendeskAuthError(Exception):
    pass


def _named(name: str) -> Exception:
    return type(name, (Exception,), {})("boom")


@pytest.mark.parametrize(
    "exc, code",
    [
        (_http_error(401), "auth"),
        (_http_error(403), "permission"),
        (_http_error(429), "rate_limit"),
        (_http_error(503), "source_unavailable"),
        (AtlassianQuotaExhausted(), "rate_limit"),
        (ZendeskAuthError("nope"), "auth"),
        (TimeoutError(), "network"),
        (RuntimeError("invalid_grant: token has been expired or revoked"), "auth"),
        (RuntimeError("something odd"), "unknown"),
        # Whole words only: "Author" is not "Auth", and "ThreadError" holds no "ReadError".
        (_named("AuthorNotFoundError"), "not_found"),
        (_named("MailThreadError"), "unknown"),
        (RuntimeError("could not parse the sslcert option"), "unknown"),
    ],
)
def test_failures_are_classified(exc: BaseException, code: str) -> None:
    assert classify_sync_error(exc)["code"] == code


def test_a_wrapped_error_is_classified_by_its_cause() -> None:
    try:
        try:
            raise _http_error(401)
        except httpx.HTTPStatusError as inner:
            raise RuntimeError("sync failed") from inner
    except RuntimeError as outer:
        assert classify_sync_error(outer)["code"] == "auth"


def test_the_stored_detail_carries_no_secrets() -> None:
    detail = classify_sync_error(
        RuntimeError(
            "GET https://api.example.com/v1/files?access_token=s3cr3t failed; "
            "refresh_token=abcd1234 Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig"
        )
    )["detail"]
    assert "s3cr3t" not in detail
    assert "abcd1234" not in detail
    assert "eyJhbGciOiJIUzI1NiJ9" not in detail


def test_a_short_bearer_token_is_scrubbed_too() -> None:
    detail = classify_sync_error(RuntimeError("Authorization: Bearer abc123def456 was refused"))["detail"]
    assert "abc123def456" not in detail


# ---- reading it back ------------------------------------------------------


def _doc(**fields) -> dict:
    return {"id": "c1", "status": AppStatus.SYNCING.value, **fields}


def test_a_snapshot_on_an_idle_connector_is_not_shown() -> None:
    snapshot = json.dumps({"runId": "r1", "lastActivityAt": get_epoch_timestamp_in_ms()})
    assert _live_run(_doc(status=AppStatus.IDLE.value, syncProgress=snapshot), get_epoch_timestamp_in_ms()) is None


def test_a_quiet_run_is_shown_as_stalled_unless_it_is_throttled() -> None:
    now = get_epoch_timestamp_in_ms()
    old = now - STALL_AFTER_MS - 1000
    quiet = _live_run(_doc(syncProgress=json.dumps({"runId": "r1", "lastActivityAt": old})), now)
    assert quiet["stalled"] is True
    waiting = _live_run(
        _doc(syncProgress=json.dumps({"runId": "r1", "lastActivityAt": old, "throttledUntil": now + 60_000})), now
    )
    assert waiting["stalled"] is False and waiting["throttledUntil"] == now + 60_000


def test_an_expired_throttle_is_not_shown() -> None:
    now = get_epoch_timestamp_in_ms()
    run = _live_run(_doc(syncProgress=json.dumps({"runId": "r1", "lastActivityAt": now, "throttledUntil": now - 1})), now)
    assert run["throttledUntil"] is None


def test_no_estimate_until_the_speed_is_known() -> None:
    assert _indexing_summary({"QUEUED": 100}, indexed_recently=5)["etaSeconds"] is None
    summary = _indexing_summary({"QUEUED": 100}, indexed_recently=100)
    assert summary["etaSeconds"] == 600


def test_queued_work_with_nothing_moving_is_waiting_in_line() -> None:
    assert _indexing_summary({"QUEUED": 10}, 0)["waitingInLine"] is True
    assert _indexing_summary({"QUEUED": 10, "IN_PROGRESS": 1}, 0)["waitingInLine"] is False


# ---- the runner -----------------------------------------------------------


def _graph_provider(doc: dict | None = None) -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value=doc or {"id": "c1", "status": "SYNCING"})
    gp.has_records_indexed_since = AsyncMock(return_value=True)
    return gp


def _idle_write(gp: AsyncMock) -> dict:
    (idle,) = [c.args[2] for c in gp.update_node.await_args_list if c.args[2].get("status") == AppStatus.IDLE.value]
    return idle


def _connector(run_sync) -> MagicMock:
    connector = MagicMock()
    connector.run_sync = run_sync
    return connector


async def test_a_failed_run_leaves_its_reason_and_history_in_the_idle_write() -> None:
    gp = _graph_provider({"id": "c1", Keys.SYNC_RUNS: json.dumps([{"runId": "older"}])})

    async def run_sync() -> None:
        progress = current_sync_progress("c1")
        progress.note_record("a", ChangeKind.NEW)
        raise _http_error(401)

    with contextlib.suppress(httpx.HTTPStatusError):
        await run_sync_task(_connector(run_sync), "c1", gp, _LOG, trigger="manual", started_by="u1")

    idle = _idle_write(gp)
    assert idle[Keys.SYNC_PROGRESS] is None
    runs = parse_run_history(idle[Keys.SYNC_RUNS])
    assert [r["runId"] for r in runs][1:] == ["older"]
    assert runs[0]["outcome"] == "failed"
    assert runs[0]["trigger"] == "manual" and runs[0]["startedBy"] == "u1"
    assert runs[0]["counts"]["new"] == 1
    assert json.loads(idle[Keys.LAST_SYNC_ERROR])["code"] == "auth"


async def test_a_successful_run_clears_the_last_failure() -> None:
    gp = _graph_provider()

    async def run_sync() -> None:
        return None

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG)
    idle = _idle_write(gp)
    assert idle[Keys.LAST_SYNC_ERROR] is None
    assert parse_run_history(idle[Keys.SYNC_RUNS])[0]["outcome"] == "succeeded"


async def test_a_new_connector_is_marked_as_awaiting_its_first_sync() -> None:
    gp = _graph_provider({"id": "c1", "type": "DRIVE"})
    gp.has_records_indexed_since = AsyncMock(return_value=False)

    async def run_sync() -> None:
        return None

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG)
    assert any(c.args[2] == {Keys.AWAITING_FIRST_SYNC: True} for c in gp.update_node.await_args_list)


async def test_a_connector_with_indexed_content_is_not_announced_again() -> None:
    gp = _graph_provider({"id": "c1", "type": "DRIVE"})

    async def run_sync() -> None:
        return None

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG)
    assert not any(Keys.AWAITING_FIRST_SYNC in c.args[2] for c in gp.update_node.await_args_list)


# ---- after a restart ------------------------------------------------------


def test_a_run_cut_off_by_a_restart_becomes_a_failed_run_in_history() -> None:
    from app.connectors_main import interrupted_run_record

    snapshot = {"runId": "r9", "trigger": "schedule", "full": True, "startedAt": 1, "counts": {"found": 4}}
    fields = interrupted_run_record(
        {Keys.SYNC_PROGRESS: json.dumps(snapshot), Keys.SYNC_RUNS: json.dumps([{"runId": "r8"}])}, 99
    )
    runs = parse_run_history(fields[Keys.SYNC_RUNS])
    assert [r["runId"] for r in runs] == ["r9", "r8"]
    assert runs[0]["outcome"] == "failed" and runs[0]["error"]["code"] == "interrupted"
    assert runs[0]["counts"] == {"found": 4}
    assert fields[Keys.SYNC_PROGRESS] is None
    assert json.loads(fields[Keys.LAST_SYNC_ERROR])["code"] == "interrupted"


def test_a_restart_with_no_snapshot_only_clears_it() -> None:
    from app.connectors_main import interrupted_run_record

    assert interrupted_run_record({}, 99) == {Keys.SYNC_PROGRESS: None}


async def test_history_unread_at_the_start_is_read_again_before_the_idle_write() -> None:
    gp = _graph_provider()
    stored = {"id": "c1", "status": "SYNCING", Keys.SYNC_RUNS: json.dumps([{"runId": "older"}])}
    reads = iter([None])
    gp.get_document = AsyncMock(side_effect=lambda **_: next(reads, stored))

    async def run_sync() -> None:
        return None

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG)
    runs = parse_run_history(_idle_write(gp)[Keys.SYNC_RUNS])
    assert [r["runId"] for r in runs][1:] == ["older"]


async def test_history_that_cannot_be_read_is_left_alone_with_the_last_failure() -> None:
    """The card reads the latest run from the history, so the last error must not move without it."""
    gp = _graph_provider()
    stored = {"id": "c1", "status": "SYNCING"}
    reads = iter([None, None])
    gp.get_document = AsyncMock(side_effect=lambda **_: next(reads, stored))

    async def run_sync() -> None:
        return None

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG)
    idle = _idle_write(gp)
    assert Keys.SYNC_RUNS not in idle and Keys.LAST_SYNC_ERROR not in idle
    assert idle[Keys.SYNC_PROGRESS] is None


async def test_a_history_re_read_that_hangs_still_ends_in_the_idle_write(monkeypatch) -> None:
    from app.connectors.core.sync import sync_runner

    monkeypatch.setattr(sync_runner, "_HISTORY_REREAD_TIMEOUT_S", 0.05)
    gp = _graph_provider()
    stored = {"id": "c1", "status": "SYNCING"}
    calls = 0

    async def get_document(**_: object) -> dict | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        if calls == 2:
            await asyncio.Event().wait()
        return stored

    gp.get_document = AsyncMock(side_effect=get_document)

    async def run_sync() -> None:
        return None

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG)
    idle = _idle_write(gp)
    assert Keys.SYNC_RUNS not in idle and Keys.LAST_SYNC_ERROR not in idle


async def test_live_progress_is_written_only_while_the_doc_is_this_runs() -> None:
    gp = _graph_provider({"id": "c1", "status": "SYNCING", Keys.LAST_SYNC_STARTED_AT: 1234})

    async def run_sync() -> None:
        current_sync_progress("c1").note_record("a", ChangeKind.NEW)
        await asyncio.sleep(0.05)

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG)
    writes = [c for c in gp.update_node_fields_if_match.await_args_list if Keys.SYNC_PROGRESS in c.args[2]]
    expected = {"status": AppStatus.SYNCING.value, Keys.LAST_SYNC_STARTED_AT: 1234}
    assert writes and all(c.args[3] == expected for c in writes)


@pytest.mark.parametrize(("runs", "has_content", "expected"), [
    (None, True, True),
    (None, False, False),
    (json.dumps([{"runId": "r1", "outcome": "succeeded"}]), False, True),
])
async def test_content_synced_before_history_was_kept_is_reported(
    runs: str | None, has_content: bool, expected: bool
) -> None:
    """An upgraded install has content but no runs; it must not read as never synced."""
    connector_id = f"c-content-{runs is None}-{has_content}"
    gp = _graph_provider({"id": connector_id, "status": "IDLE", Keys.SYNC_RUNS: runs})
    gp.has_records_indexed_since = AsyncMock(return_value=has_content)
    gp.count_connector_records_by_status = AsyncMock(return_value={})
    gp.count_records_indexed_since = AsyncMock(return_value=0)

    view = await build_progress_view(gp, "o1", connector_id)
    assert view["hasSearchableContent"] is expected
    if runs:
        gp.has_records_indexed_since.assert_not_awaited()


def _failed_start_write(gp: AsyncMock) -> tuple[dict, dict]:
    (call,) = gp.update_node_fields_if_match.await_args_list
    return call.args[2], call.args[3]


async def test_a_sync_that_could_not_start_is_a_failed_run_with_its_reason() -> None:
    """An expired sign-in fails init, so no run ever starts; the card must still say why."""
    good = json.dumps([{"runId": "r0", "outcome": "succeeded", "counts": {"found": 9}}])
    gp = _graph_provider({"id": "c1", "status": "IDLE", Keys.SYNC_RUNS: good})
    await record_failed_start(gp, _LOG, "c1", _http_error(401), trigger="schedule")

    written, expected = _failed_start_write(gp)
    runs = json.loads(written[Keys.SYNC_RUNS])
    assert [r["outcome"] for r in runs] == ["failed", "succeeded"]
    assert runs[0]["trigger"] == "schedule" and runs[0]["error"]["code"] == "auth"
    assert json.loads(written[Keys.LAST_SYNC_ERROR])["code"] == "auth"
    assert expected == {"status": "IDLE", Keys.SYNC_RUNS: good}


async def test_the_same_failed_start_again_replaces_its_entry() -> None:
    """Retries of a failing init must not push the real runs out of the history."""
    earlier = json.dumps([
        {"runId": "f1", "outcome": "failed", "error": {"code": "auth"}, "counts": {"found": 0}},
        {"runId": "r0", "outcome": "succeeded", "counts": {"found": 9}},
    ])
    gp = _graph_provider({"id": "c1", "status": "IDLE", Keys.SYNC_RUNS: earlier})
    await record_failed_start(gp, _LOG, "c1", _http_error(401), trigger="startup")

    runs = json.loads(_failed_start_write(gp)[0][Keys.SYNC_RUNS])
    assert [(r["runId"], r["outcome"]) for r in runs] == [("f1", "failed"), ("r0", "succeeded")]


@pytest.mark.parametrize("status", ["SYNCING", "DELETING"])
async def test_a_failed_start_leaves_a_running_or_departing_connector_alone(status: str) -> None:
    gp = _graph_provider({"id": "c1", "status": status})
    await record_failed_start(gp, _LOG, "c1", RuntimeError("x"), trigger="schedule")
    gp.update_node_fields_if_match.assert_not_awaited()


async def test_recording_a_failed_start_never_raises() -> None:
    gp = _graph_provider()
    gp.get_document = AsyncMock(side_effect=RuntimeError("db down"))
    await record_failed_start(gp, _LOG, "c1", None, trigger="startup")


# ---- first sync ready -----------------------------------------------------


def _watch_provider(app: dict, pending: dict, claimed: bool = True) -> AsyncMock:
    gp = AsyncMock()
    gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    gp.get_org_apps = AsyncMock(return_value=[app])
    gp.count_connector_records_by_status = AsyncMock(side_effect=[pending, {"COMPLETED": 1200}])
    gp.update_node_fields_if_match = AsyncMock(return_value=claimed)
    return gp


def _ready_app(**fields) -> dict:
    return {
        "_key": "c1", "name": "Drive", "type": "DRIVE", "isActive": True, "status": "IDLE",
        "createdBy": "u1", "lastSyncedBy": "u2",
        Keys.AWAITING_FIRST_SYNC: True, Keys.LAST_SYNC_OUTCOME: "succeeded", **fields,
    }


async def test_a_first_sync_is_announced_once_everything_is_searchable() -> None:
    from app.connectors.core.sync.first_sync_watch import check_first_syncs

    gp = _watch_provider(_ready_app(), {})
    notifications = AsyncMock()
    assert await check_first_syncs(gp, notifications, _LOG) == ["c1"]
    sent = notifications.publish_notification.await_args.kwargs
    assert sent["recipient_user_ids"] == ["u1", "u2"]
    assert "1,200" in sent["message"]


async def test_a_notice_the_broker_refused_is_tried_again() -> None:
    from app.connectors.core.sync.first_sync_watch import check_first_syncs

    gp = _watch_provider(_ready_app(), {})
    notifications = AsyncMock()
    notifications.publish_notification = AsyncMock(return_value=False)
    assert await check_first_syncs(gp, notifications, _LOG) == []
    claim, give_back = gp.update_node_fields_if_match.await_args_list
    assert give_back.args[2] == {Keys.AWAITING_FIRST_SYNC: True, Keys.READY_AT: None}
    assert give_back.args[3] == {Keys.AWAITING_FIRST_SYNC: False}


@pytest.mark.parametrize(
    "app, pending, claimed",
    [
        (_ready_app(), {"QUEUED": 3}, True),
        (_ready_app(status="SYNCING"), {}, True),
        (_ready_app(**{Keys.LAST_SYNC_OUTCOME: "failed"}), {}, True),
        (_ready_app(isActive=False), {}, True),
        (_ready_app(), {}, False),
    ],
)
async def test_no_announcement_while_not_ready_or_already_claimed(app: dict, pending: dict, claimed: bool) -> None:
    from app.connectors.core.sync.first_sync_watch import check_first_syncs

    notifications = AsyncMock()
    assert await check_first_syncs(_watch_provider(app, pending, claimed), notifications, _LOG) == []
    notifications.publish_notification.assert_not_awaited()


# ---- why records are not searchable ---------------------------------------


def test_stats_group_unsearchable_records_by_reason() -> None:
    from app.services.graph_db.common.utils import build_connector_stats_response

    rows = [
        {"recordType": "FILE", "indexingStatus": "FAILED", "reason": "Too large", "cnt": 2},
        {"recordType": "MAIL", "indexingStatus": "FAILED", "reason": "Too large", "cnt": 3},
        {"recordType": "FILE", "indexingStatus": "EMPTY", "reason": None, "cnt": 1},
        {"recordType": "FILE", "indexingStatus": "COMPLETED", "reason": None, "cnt": 9},
    ]
    result = build_connector_stats_response(rows, ["FAILED", "EMPTY", "COMPLETED"], "o", "c")
    assert result["byReason"] == [
        {"indexingStatus": "FAILED", "reason": "Too large", "count": 5},
        {"indexingStatus": "EMPTY", "reason": None, "count": 1},
    ]
    assert result["stats"]["total"] == 15

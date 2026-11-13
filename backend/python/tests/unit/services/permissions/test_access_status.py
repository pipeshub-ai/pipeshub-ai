"""The admin freshness status: every field equals what the connector's app document says, for each state, and names nothing."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.services.permissions import access_status as a
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    set_resolved_generation,
)
from app.services.permissions.structure_generation import (
    GENERATION_KEY,
    PENDING_KEY,
    UNSAFE_KEY,
)
from app.services.vector_db.scope_backfill import BACKFILL_KEYS as K
from app.services.vector_db.scope_backfill import DONE
from app.services.vector_db.scope_sweep import SWEEP_KEY

FINISHED_MS = 1700000000000
FINISHED = "2023-11-14T22:13:20+00:00"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def acl_generation():  # noqa: ANN201
    """The ACL scope generation the fixtures are stamped with."""
    set_resolved_generation(3)
    yield
    set_resolved_generation(None)


def healthy(**over: object) -> dict:
    """A connector whose last sync succeeded and whose stamps are current."""
    app = {K["state"]: DONE, K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: 3, K["structure_generation"]: 5, GENERATION_KEY: 5,
           K["records"]: 120, "lastSyncFinishedAtTimestamp": FINISHED_MS, "lastSyncOutcome": "succeeded", "status": "IDLE"}
    return {**app, **over}


def st(app: dict, **kw: object) -> dict:
    """The status of ``app`` at a fixed time."""
    return a.status(app, now=NOW, **kw)


def test_healthy() -> None:
    """Healthy."""
    out = st(healthy(), reach_minutes=60)
    assert out == {
        "asOf": "2026-11-11T12:00:00+00:00", "state": "healthy", "reachWithinMinutes": 60,
        "serving": {"current": True, "served": True},
        "lastPermissionSync": {"finishedAt": FINISHED, "outcome": "succeeded"},
        "pending": {"roots": 0, "unreadable": False, "oldestChangesAgo": None},
        "coverage": {"records": 120, "stale": 0}, "attention": [], "warnings": [],
    }


def test_running_is_served_by_nobody_and_wins_over_everything() -> None:
    """Running is served by nobody and wins over everything."""
    out = st(healthy(status="FULL_SYNCING", lastSyncOutcome="failed", **{PENDING_KEY: '{"r": 1}'}))
    assert out["state"] == "running" and out["lastPermissionSync"]["outcome"] == "running"
    assert out["serving"] == {"current": True, "served": False}      # stamps are current, but roots are pending and a sync is running


def test_failed_keeps_the_time_of_the_run() -> None:
    """Failed keeps the time of the run."""
    out = st(healthy(lastSyncOutcome="failed"))
    assert out["state"] == "failed" and out["lastPermissionSync"] == {"finishedAt": FINISHED, "outcome": "failed"}


def test_never_when_no_sync_has_ended() -> None:
    """Never when no sync has ended."""
    out = st(healthy(lastSyncFinishedAtTimestamp=None, lastSyncOutcome=None))
    assert out["state"] == "never" and out["lastPermissionSync"] == {"finishedAt": None, "outcome": "never"}


@pytest.mark.parametrize("stored", [float(FINISHED_MS), str(FINISHED_MS)])
def test_a_store_that_returns_the_time_as_a_float_or_text_still_reads(stored: object) -> None:
    """A store that returns the time as a float or text still reads."""
    assert st(healthy(lastSyncFinishedAtTimestamp=stored))["lastPermissionSync"]["finishedAt"] == FINISHED


@pytest.mark.parametrize("stored", ["soon", -5, True, 1e400])
def test_an_unreadable_time_is_never_not_a_crash(stored: object) -> None:
    """An unreadable time is never not a crash."""
    assert st(healthy(lastSyncFinishedAtTimestamp=stored))["state"] == "never"


def test_pending_roots_count_and_how_many_changes_ago_without_naming_them() -> None:
    """Pending roots count and how many changes ago without naming them."""
    out = st(healthy(**{GENERATION_KEY: 12, PENDING_KEY: json.dumps({"rootA": 9, "rootB": 11})}))
    assert out["state"] == "pending"
    assert out["pending"] == {"roots": 2, "unreadable": False, "oldestChangesAgo": 3}
    assert "pending_roots" not in out["attention"]       # the roots have their own row
    assert "rootA" not in str(out)


def test_unreadable_pending_roots_are_not_pending_and_say_so_in_attention() -> None:
    """Unreadable pending roots are not pending and say so in attention."""
    out = st(healthy(**{PENDING_KEY: "not json"}))
    assert out["state"] == "healthy"        # not "pending": nothing is known to wait, and a connector that is not current hides nothing
    assert out["pending"] == {"roots": 0, "unreadable": True, "oldestChangesAgo": None}
    assert out["attention"] == ["pending_unreadable"] and out["serving"]["current"] is False


def test_overflow_is_a_change_too_large_to_list_as_subtrees() -> None:
    """The structure generation is ahead of the stamped one and no root was registered (more than the cap, or a change that reaches the whole connector)."""
    out = st(healthy(**{GENERATION_KEY: 6}))
    assert out["state"] == "overflow" and out["attention"] == ["needs_full_pass"]
    assert out["pending"]["roots"] == 0 and out["serving"]["served"] is False


@pytest.mark.parametrize("over", [
    {K["state"]: "failed"},
    {K["state"]: "running"},
    {K["version"]: SCOPE_FIELDS_VERSION - 1},
    {K["generation"]: 2},
    {UNSAFE_KEY: 99},
], ids=["failed back-fill", "running back-fill", "older field version", "other ACL generation", "unsafe mark"])
def test_a_connector_that_is_not_current_for_another_reason_is_not_overflow(over: dict) -> None:
    """A connector that is not current for another reason is not overflow."""
    out = st(healthy(**over))
    assert out["state"] == "healthy" and out["serving"]["current"] is False
    assert out["attention"] == ["needs_full_pass"]


def test_structure_ahead_of_an_unreadable_stamp_is_not_overflow() -> None:
    """Structure ahead of an unreadable stamp is not overflow."""
    assert st(healthy(**{GENERATION_KEY: 6, K["structure_generation"]: None}))["state"] == "healthy"
    assert st(healthy(**{GENERATION_KEY: 6, PENDING_KEY: '{"r": 6}'}))["state"] == "pending"      # a root was registered: a subtree rewrite, not the whole connector


def test_an_unread_generation_does_not_invent_an_age() -> None:
    """An unread generation does not invent an age."""
    assert st(healthy(**{GENERATION_KEY: "x", PENDING_KEY: '{"r": 1}'}))["pending"]["oldestChangesAgo"] is None


def test_attention_lists_codes_only_and_stale_points_show_in_coverage() -> None:
    """Attention lists codes only and stale points show in coverage."""
    sweep = json.dumps({"n": 2, "e": "secret project name", "t": 0})
    out = st(healthy(**{K["stale"]: 4, SWEEP_KEY: sweep}))
    assert out["attention"] == ["stale_points", "sweep_failing"]
    assert out["coverage"] == {"records": 120, "stale": 4}
    assert "secret" not in str(out)


def test_no_back_fill_has_no_coverage_and_is_not_served() -> None:
    """No back fill has no coverage and is not served."""
    out = st({"lastSyncFinishedAtTimestamp": FINISHED_MS, "lastSyncOutcome": "succeeded"})
    assert out["coverage"] is None and out["serving"] == {"current": False, "served": False} and out["state"] == "healthy"


def test_connector_warnings_pass_through_trimmed_and_bad_entries_are_dropped() -> None:
    """Connector warnings pass through trimmed and bad entries are dropped."""
    raw = [{"code": "scheme_unreadable", "items": 4, "container": "HR", "fix": {"vendor": "Jira"}, "secret": "x"}, {"items": 1}, "x", None]
    out = st(healthy(permissionWarnings=raw))
    assert out["warnings"] == [{"code": "scheme_unreadable", "items": 4, "container": "HR", "fix": {"vendor": "Jira"}}]
    assert st(healthy(permissionWarnings="oops"))["warnings"] == []

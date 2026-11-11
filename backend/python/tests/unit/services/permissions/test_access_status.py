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
from app.services.permissions.structure_generation import GENERATION_KEY, PENDING_KEY
from app.services.vector_db.scope_backfill import BACKFILL_KEYS as K
from app.services.vector_db.scope_backfill import DONE
from app.services.vector_db.scope_sweep import SWEEP_KEY

FINISHED_MS = 1700000000000
FINISHED = "2023-11-14T22:13:20+00:00"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def acl_generation():  # noqa: ANN201
    set_resolved_generation(3)
    yield
    set_resolved_generation(None)


def healthy(**over: object) -> dict:
    """A connector whose last sync succeeded and whose stamps are current."""
    app = {K["state"]: DONE, K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: 3, K["structure_generation"]: 5, GENERATION_KEY: 5,
           K["records"]: 120, "lastSyncFinishedAtTimestamp": FINISHED_MS, "lastSyncOutcome": "succeeded", "status": "IDLE"}
    return {**app, **over}


def st(app: dict, **kw: object) -> dict:
    return a.status(app, now=NOW, **kw)


def test_healthy() -> None:
    out = st(healthy(), reach_minutes=60)
    assert out == {
        "asOf": "2026-11-11T12:00:00+00:00", "state": "healthy", "reachWithinMinutes": 60,
        "serving": {"current": True, "served": True},
        "lastPermissionSync": {"finishedAt": FINISHED, "outcome": "succeeded"},
        "pending": {"roots": 0, "unreadable": False, "oldestChangesAgo": None},
        "coverage": {"records": 120, "stale": 0}, "attention": [], "warnings": [],
    }


def test_running_is_served_by_nobody_and_wins_over_everything() -> None:
    out = st(healthy(status="FULL_SYNCING", lastSyncOutcome="failed", **{PENDING_KEY: '{"r": 1}'}))
    assert out["state"] == "running" and out["lastPermissionSync"]["outcome"] == "running"
    assert out["serving"] == {"current": True, "served": False}      # stamps are current, but roots are pending and a sync is running


def test_failed_keeps_the_time_of_the_run() -> None:
    out = st(healthy(lastSyncOutcome="failed"))
    assert out["state"] == "failed" and out["lastPermissionSync"] == {"finishedAt": FINISHED, "outcome": "failed"}


def test_never_when_no_sync_has_ended() -> None:
    out = st(healthy(lastSyncFinishedAtTimestamp=None, lastSyncOutcome=None))
    assert out["state"] == "never" and out["lastPermissionSync"] == {"finishedAt": None, "outcome": "never"}


@pytest.mark.parametrize("stored", [float(FINISHED_MS), str(FINISHED_MS)])
def test_a_store_that_returns_the_time_as_a_float_or_text_still_reads(stored: object) -> None:
    assert st(healthy(lastSyncFinishedAtTimestamp=stored))["lastPermissionSync"]["finishedAt"] == FINISHED


@pytest.mark.parametrize("stored", ["soon", -5, True, 1e400])
def test_an_unreadable_time_is_never_not_a_crash(stored: object) -> None:
    assert st(healthy(lastSyncFinishedAtTimestamp=stored))["state"] == "never"


def test_pending_roots_count_and_how_many_changes_ago_without_naming_them() -> None:
    out = st(healthy(**{GENERATION_KEY: 12, PENDING_KEY: json.dumps({"rootA": 9, "rootB": 11})}))
    assert out["state"] == "pending"
    assert out["pending"] == {"roots": 2, "unreadable": False, "oldestChangesAgo": 3}
    assert "pending_roots" not in out["attention"]       # the roots have their own row
    assert "rootA" not in str(out)


def test_unreadable_pending_roots_are_pending_and_need_attention() -> None:
    out = st(healthy(**{PENDING_KEY: "not json"}))
    assert out["state"] == "pending" and out["pending"] == {"roots": 0, "unreadable": True, "oldestChangesAgo": None}
    assert out["attention"] == ["pending_unreadable"]


def test_overflow_is_a_change_too_large_to_list_as_subtrees() -> None:
    """The generation moved and no root was registered (more than the cap, or a change that reaches the whole connector)."""
    out = st(healthy(**{GENERATION_KEY: 6}))
    assert out["state"] == "overflow" and out["attention"] == ["needs_full_pass"]
    assert out["pending"]["roots"] == 0 and out["serving"]["served"] is False


def test_an_unread_generation_does_not_invent_an_age() -> None:
    assert st(healthy(**{GENERATION_KEY: "x", PENDING_KEY: '{"r": 1}'}))["pending"]["oldestChangesAgo"] is None


def test_attention_lists_codes_only_and_stale_points_show_in_coverage() -> None:
    sweep = json.dumps({"n": 2, "e": "secret project name", "t": 0})
    out = st(healthy(**{K["stale"]: 4, SWEEP_KEY: sweep}))
    assert out["attention"] == ["stale_points", "sweep_failing"]
    assert out["coverage"] == {"records": 120, "stale": 4}
    assert "secret" not in str(out)


def test_no_back_fill_has_no_coverage_and_is_not_served() -> None:
    out = st({"lastSyncFinishedAtTimestamp": FINISHED_MS, "lastSyncOutcome": "succeeded"})
    assert out["coverage"] is None and out["serving"] == {"current": False, "served": False} and out["state"] == "healthy"


def test_connector_warnings_pass_through_trimmed_and_bad_entries_are_dropped() -> None:
    raw = [{"code": "scheme_unreadable", "items": 4, "container": "HR", "fix": {"vendor": "Jira"}, "secret": "x"}, {"items": 1}, "x", None]
    out = st(healthy(permissionWarnings=raw))
    assert out["warnings"] == [{"code": "scheme_unreadable", "items": 4, "container": "HR", "fix": {"vendor": "Jira"}}]
    assert st(healthy(permissionWarnings="oops"))["warnings"] == []

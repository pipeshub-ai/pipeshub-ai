"""A Drive run that left a gap names what went wrong: its last line used to say only that part of Drive was not read,
also when a group or a file could not be saved."""

from __future__ import annotations

import pytest

from app.connectors.sources.google.common.connector_google_exceptions import (
    DriveSyncIncomplete,
)
from app.connectors.sources.google.drive.utils.drive_full_sync_reconciler import (
    DriveItemsUnread,
    FullSyncLedger,
    incomplete_run_error,
)


class _SaveFailedError(Exception):
    pass


def _fail(ledger: FullSyncLedger, error: Exception, *, item: str | None = None) -> None:
    try:
        raise error
    except Exception:
        if item:
            ledger.fail_item(item)
        else:
            ledger.note_incomplete()


def test_the_errors_behind_the_gaps_are_named_with_counts() -> None:
    ledger = FullSyncLedger()
    _fail(ledger, _SaveFailedError("secret@example.com"))
    _fail(ledger, _SaveFailedError("another"))
    _fail(ledger, TimeoutError(), item="f1")

    text = str(incomplete_run_error(ledger))

    assert "_SaveFailedError (2), TimeoutError (1)" in text
    assert "could not be read or saved" in text and "next run" in text
    assert "secret" not in text, "an error's text can name people and files"


def test_only_the_most_common_are_named() -> None:
    ledger = FullSyncLedger()
    for i in range(20):
        _fail(ledger, type(f"E{i}Error", (Exception,), {})())
    assert str(incomplete_run_error(ledger)).count("Error (") == 5


def test_a_gap_with_no_error_is_still_reported() -> None:
    ledger = FullSyncLedger()
    ledger.note_incomplete()
    ledger.note_skipped_drive()
    text = str(incomplete_run_error(ledger))
    assert "could not be read or saved in this run;" in text and "(" not in text


def test_a_gap_no_error_is_behind_can_be_named() -> None:
    ledger = FullSyncLedger()
    ledger.note_incomplete("a selected folder could not be listed by any synced user")
    assert "in this run: a selected folder could not be listed by any synced user (1);" in str(incomplete_run_error(ledger))


@pytest.mark.parametrize(("items_only", "kind"), [(True, DriveItemsUnread), (False, DriveSyncIncomplete)])
def test_the_kind_of_error_is_unchanged(items_only: bool, kind: type) -> None:
    ledger = FullSyncLedger()
    _fail(ledger, TimeoutError(), item="f1" if items_only else None)
    assert type(incomplete_run_error(ledger)) is kind

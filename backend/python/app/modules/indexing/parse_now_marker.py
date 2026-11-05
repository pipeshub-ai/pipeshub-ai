"""The parse-now marker on a record, and the store-only run mode.

A ``reindexRecord`` event carrying ``parseNow`` asks indexing to parse and store a record's
content now, so a reader can have it before the record is embedded. The outcome is recorded
on the record document in three fields:

* ``parseNowStatus``: ``QUEUED`` | ``IN_PROGRESS`` | ``STORED`` | ``FAILED`` | ``EMPTY`` |
  ``PASSWORD_PROTECTED`` | ``FILE_TYPE_NOT_SUPPORTED`` | ``NEEDS_MULTIMODAL``
* ``parseNowRev``: the ``contentRev`` the status applies to
* ``parseNowReason``: why, for the statuses that are not ``STORED``
* ``parseNowAt``: when the status was written (epoch milliseconds, UTC)

A record whose automatic indexing is off is parsed in *store-only* mode: its content is stored
and the marker written, but nothing is embedded and none of its own statuses change, so a later
manual index starts from a record exactly as the admin left it. Every write of such a record's
status during the run goes through ``shape_status_write``, which turns it into marker fields.

The run is held in a context variable: the handler's whole event chain (handler, event processor,
processor, pipeline, sink) runs inside one task, and the variable lives and dies with that task.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import ProgressStatus
from app.modules.transformers.transformer import PARSE_NOW, STORE_ONLY
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Mapping

PARSE_NOW_STATUS = "parseNowStatus"
PARSE_NOW_REV = "parseNowRev"
PARSE_NOW_REASON = "parseNowReason"
PARSE_NOW_AT = "parseNowAt"


class ParseNowStatus(StrEnum):
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    STORED = "STORED"
    FAILED = "FAILED"
    EMPTY = "EMPTY"
    PASSWORD_PROTECTED = "PASSWORD_PROTECTED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    NEEDS_MULTIMODAL = "NEEDS_MULTIMODAL"


# What a record's indexing status means for the marker. Anything not listed (COMPLETED, AUTO_INDEX_OFF,
# SKIPPED, ...) says nothing about the parse.
_MARKER_OF_INDEXING_STATUS: dict[str, ParseNowStatus] = {
    ProgressStatus.IN_PROGRESS.value: ParseNowStatus.IN_PROGRESS,
    ProgressStatus.QUEUED.value: ParseNowStatus.QUEUED,
    ProgressStatus.NOT_STARTED.value: ParseNowStatus.QUEUED,
    ProgressStatus.FAILED.value: ParseNowStatus.FAILED,
    ProgressStatus.EMPTY.value: ParseNowStatus.EMPTY,
    ProgressStatus.PASSWORD_PROTECTED.value: ParseNowStatus.PASSWORD_PROTECTED,
    ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value: ParseNowStatus.FILE_TYPE_NOT_SUPPORTED,
    ProgressStatus.ENABLE_MULTIMODAL_MODELS.value: ParseNowStatus.NEEDS_MULTIMODAL,
}

# Fields a status write carries that belong to the record's indexing state, not to its content.
_STATUS_FIELDS = frozenset({
    "indexingStatus",
    "parsingStatus",
    "extractionStatus",
    "processingStartedAt",
    "queuedAtTimestamp",
    "isDirty",
    "lastIndexTimestamp",
    "lastExtractionTimestamp",
    "hasIndexedCopy",
    "extractionDeferred",
    "reason",
    "retryNotBefore",
})


@dataclass
class ParseNowRun:
    """The parse-now run in progress in this task."""

    store_only: bool
    # The revision of the content being parsed, once the handler has downloaded it.
    content_rev: str | None = None
    # The sink has made the content durable and marked it STORED; a later status write must not hide it.
    stored: bool = False


_run: ContextVar[ParseNowRun | None] = ContextVar("parse_now_run", default=None)


def current_run() -> ParseNowRun | None:
    return _run.get()


def store_only_active() -> bool:
    run = _run.get()
    return run is not None and run.store_only


def begin_run(run: ParseNowRun) -> None:
    _run.set(run)


def end_run() -> None:
    # Set back rather than reset by token: an async generator closed from another context cannot reset.
    _run.set(None)


def run_settings() -> dict[str, Any]:
    """``TransformContext.settings`` for the run in progress; empty outside one."""
    run = _run.get()
    if run is None:
        return {}
    settings: dict[str, Any] = {PARSE_NOW: True}
    if run.store_only:
        settings[STORE_ONLY] = True
    return settings


def marker_fields(status: ParseNowStatus, rev: str | None, reason: str | None = None) -> dict[str, Any]:
    return {PARSE_NOW_STATUS: status.value, PARSE_NOW_REV: rev, PARSE_NOW_REASON: reason, PARSE_NOW_AT: get_epoch_timestamp_in_ms()}


def shape_status_write(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The fields to persist for a write of a record's indexing state during a parse-now run.

    Outside a run, as given. In a normal run, as given plus the marker for the status written. In a
    store-only run the record's own status fields are dropped and only the marker (and any other
    field, such as ``contentRev`` or ``virtualRecordId``) is written.
    """
    run = _run.get()
    if run is None:
        return dict(fields)
    rev = fields.get("contentRev") or run.content_rev
    if fields.get("contentRev"):
        run.content_rev = fields["contentRev"]
    marker = _MARKER_OF_INDEXING_STATUS.get(str(fields.get("indexingStatus") or ""))
    shaped = (
        {k: v for k, v in fields.items() if k not in _STATUS_FIELDS} if run.store_only else dict(fields)
    )
    if marker is not None and not run.stored:
        reason = fields.get("reason") if marker is not ParseNowStatus.IN_PROGRESS else None
        shaped.update(marker_fields(marker, rev, reason or None))
    return shaped

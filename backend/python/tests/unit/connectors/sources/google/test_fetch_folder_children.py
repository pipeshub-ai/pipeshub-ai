"""A folder's children are listed in full, or the listing says it was not."""

import logging

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleDriveError,
)
from app.connectors.sources.google.drive.utils.drive_pagination import DrivePageWalk
from app.connectors.sources.google.drive.utils.folder_filter_utils import (
    build_tracked_folder_ids,
    fetch_folder_children,
)


def _http_error(status: int, reason: str | None = None) -> HttpError:
    error = HttpError(httplib2.Response({"status": status}), b"{}")
    error.error_details = [{"reason": reason}] if reason else []
    return error


class _Drive:
    def __init__(self, page: object, probe: object = None) -> None:
        self.page = page
        self.probe = probe
        self.gets: list[dict] = []
        self.lists: list[dict] = []

    async def files_get(self, **params: object) -> object:
        self.gets.append(params)
        if isinstance(self.probe, Exception):
            raise self.probe
        return self.probe or {}

    async def files_list(self, **params: object) -> object:
        self.lists.append(params)
        return self.page


async def _children(drive: _Drive, **kwargs: object) -> list[dict]:
    async def provider() -> _Drive:
        return drive

    found: list[dict] = []
    async for batch in fetch_folder_children("folder", set(), provider, fields="files(id)", **kwargs):
        found.extend(batch)
    return found


async def test_a_rate_limited_drive_lookup_fails_instead_of_listing_the_user_corpus() -> None:
    drive = _Drive({"files": []}, probe=_http_error(403, "rateLimitExceeded"))

    with pytest.raises(HttpError):
        await _children(drive)

    assert drive.lists == []


async def test_a_folder_that_is_gone_falls_back_to_the_user_corpus() -> None:
    drive = _Drive({"files": [{"id": "child"}]}, probe=_http_error(404))

    assert [c["id"] for c in await _children(drive)] == ["child"]
    assert "driveId" not in drive.lists[0]


async def test_a_known_drive_skips_the_lookup_and_lists_that_drive() -> None:
    drive = _Drive({"files": [{"id": "child"}]}, probe=_http_error(403, "rateLimitExceeded"))

    await _children(drive, folder_drive_id="sd-1")

    assert drive.gets == []
    assert drive.lists[0]["corpora"] == "drive"
    assert drive.lists[0]["driveId"] == "sd-1"


async def test_an_incomplete_page_raises_when_there_is_no_walk_to_record_it() -> None:
    drive = _Drive({"files": [{"id": "child"}], "incompleteSearch": True})

    with pytest.raises(GoogleDriveError):
        await _children(drive, drive_scoped=False)


async def test_an_incomplete_page_marks_the_walk_and_keeps_its_children() -> None:
    drive = _Drive({"files": [{"id": "child"}], "incompleteSearch": True})
    walk = DrivePageWalk()

    assert [c["id"] for c in await _children(drive, drive_scoped=False, walk=walk)] == ["child"]
    assert walk.incomplete


@pytest.mark.parametrize(
    "page",
    [{"files": [{"id": "sub"}], "incompleteSearch": True}, ["not", "a", "page"]],
    ids=["incompleteSearch", "non-dict"],
)
async def test_a_partial_subfolder_page_fails_the_expansion(page: object) -> None:
    drive = _Drive(page)

    async def provider() -> _Drive:
        return drive

    with pytest.raises(GoogleDriveError):
        await build_tracked_folder_ids(["pick"], provider, logging.getLogger(__name__))

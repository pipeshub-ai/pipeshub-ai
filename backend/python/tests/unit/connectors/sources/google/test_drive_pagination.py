"""Drive list pagination keeps going when a page is empty but still has a token."""

import logging

import pytest

from app.connectors.sources.google.drive.utils.drive_pagination import (
    DrivePageWalk,
    iter_drive_pages,
)


async def test_an_empty_page_with_a_token_is_not_the_end() -> None:
    pages = [
        {"files": [], "nextPageToken": "page-2"},
        {"files": [{"id": "f1"}], "nextPageToken": None},
    ]
    calls: list[dict] = []

    async def fetch(**params: object) -> dict:
        calls.append(dict(params))
        return pages[len(calls) - 1]

    seen = [page async for page in iter_drive_pages(fetch, {"pageSize": 1000}, "files")]

    assert seen == [[], [{"id": "f1"}]]
    assert calls[1]["pageToken"] == "page-2"
    assert "pageToken" not in calls[0]


async def test_a_repeated_page_token_stops_the_walk() -> None:
    walk = DrivePageWalk()

    async def fetch(**_params: object) -> dict:
        return {"files": [{"id": "f1"}], "nextPageToken": "again"}

    pages = [
        page
        async for page in iter_drive_pages(
            fetch, {}, "files", walk=walk
        )
    ]

    assert pages == [[{"id": "f1"}], [{"id": "f1"}]]
    assert walk.stopped_on_repeat is True
    assert walk.incomplete is True


async def test_incomplete_search_is_logged_and_only_when_the_flag_is_true(caplog: pytest.LogCaptureFixture) -> None:
    walk = DrivePageWalk()

    async def fetch(**_params: object) -> dict:
        return {"files": [], "incompleteSearch": True}

    with caplog.at_level(logging.WARNING):
        pages = [page async for page in iter_drive_pages(fetch, {}, "files", logging.getLogger("drive-pages"), walk)]

    assert pages == [[]]
    assert walk.incomplete is True
    assert "incompleteSearch" in caplog.text


async def test_a_non_dict_page_stops_instead_of_looping() -> None:
    walk = DrivePageWalk()

    async def fetch(**_params: object) -> object:
        return object()

    pages = [page async for page in iter_drive_pages(fetch, {}, "files", walk=walk)]

    assert pages == []
    assert walk.incomplete is True

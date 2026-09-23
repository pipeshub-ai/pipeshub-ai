"""The reason a person sees when a crawled page could not be fetched."""

import pytest

from app.connectors.sources.web.connector import failed_page_reason


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (404, "The page wasn't found (404 Not Found). Check the URL is correct, then sync again."),
        (
            403,
            "The page refused access (403 Forbidden). It may need a login or block automated visitors; "
            "make sure it's publicly reachable, then sync again.",
        ),
        (503, "The site didn't respond properly (503 Service Unavailable). PipesHub will try again on the next sync."),
        (429, "The site didn't respond properly (429 Too Many Requests). PipesHub will try again on the next sync."),
        (
            418,
            "The page returned an error (418 I'm a Teapot). Check the URL is correct and publicly reachable, "
            "then sync again.",
        ),
    ],
)
def test_known_statuses_say_what_happened_and_what_to_do(status_code: int, expected: str) -> None:
    assert failed_page_reason(status_code) == expected


@pytest.mark.parametrize("status_code", [None, 0, 999])
def test_no_usable_status_means_the_page_could_not_be_reached(status_code: int | None) -> None:
    assert failed_page_reason(status_code) == (
        "We couldn't reach this page. Check the URL is correct and publicly reachable, then sync again."
    )

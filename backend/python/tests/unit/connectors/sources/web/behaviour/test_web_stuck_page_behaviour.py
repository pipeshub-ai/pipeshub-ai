"""A page whose request never finishes is given up on at its deadline: the crawl goes on, the page
is shown as failed with the same reason as any page that never answered, and the next sync tries
it again.

The curl_cffi strategy serves the pages here. Its fake session either never returns for the stuck
page (a wedged request) or hands curl's content callback one byte at a time, forever (a trickle).
"""

import asyncio
import threading
from collections.abc import Callable, Iterator

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeRecordsDb,
    FakeRequestsClient,
    FakeResponse,
    FakeWeb,
    MakeConnector,
    Page,
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.sources.web import fetch_strategy

STUCK = "http://site.test/stuck"
FINE = "http://site.test/fine"
UNREACHABLE = "We couldn't reach this page. Check the URL is correct and publicly reachable, then sync again."


class StuckSite:
    """What the fake curl sessions do with the stuck page."""

    def __init__(self) -> None:
        self.mode: str | None = None  # "wedge", "trickle", or None to answer normally
        self.release = threading.Event()
        self.attempts = 0
        self.trickles_running = 0
        self._lock = threading.Lock()

    def wedge(self) -> None:
        self.attempts += 1
        self.release.wait(30)
        raise ConnectionError("released")

    def trickle(self, content_callback: Callable[[bytes], object]) -> None:
        from curl_cffi.curl import CURL_WRITEFUNC_ERROR
        from curl_cffi.requests.exceptions import RequestException

        self.attempts += 1
        with self._lock:
            self.trickles_running += 1
        try:
            for _ in range(1500):
                if content_callback(b"x") == CURL_WRITEFUNC_ERROR:
                    response = FakeResponse(200, {"Content-Type": "text/html"}, b"", STUCK)
                    raise RequestException("Failure writing output to destination", 23, response)
                self.release.wait(0.02)
            raise ConnectionError("trickled to the end")
        finally:
            with self._lock:
                self.trickles_running -= 1


@pytest.fixture
def stuck(site: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> Iterator[StuckSite]:
    import curl_cffi.requests

    state = StuckSite()

    class Session(FakeRequestsClient):
        def get(self, url: str, headers: dict | None = None, timeout: object = None,
                allow_redirects: bool = True, stream: bool = False,
                content_callback: Callable[[bytes], object] | None = None) -> FakeResponse:
            if state.mode and url.rstrip("/") == STUCK:
                site.clients.append(("curl_cffi", url))
                if state.mode == "wedge" or content_callback is None:
                    state.wedge()
                state.trickle(content_callback)
            return super().get(url, headers, timeout, allow_redirects, stream, content_callback)

    monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])
    monkeypatch.setattr(curl_cffi.requests, "Session", lambda **_: Session(site, "curl_cffi"))
    monkeypatch.setattr(fetch_strategy, "_hop_deadline", lambda timeout: 0.3, raising=False)
    site.html(START_URL, "Home", "/stuck", "/fine")
    site.html(FINE, "Fine")
    # The page doesn't answer the aiohttp strategy either, so no strategy gets it.
    site.add(STUCK, Page(hang_up=True))
    try:
        yield state
    finally:
        state.release.set()


@pytest.mark.parametrize("mode", ["wedge", "trickle"])
async def test_a_page_that_never_finishes_fails_and_the_crawl_goes_on(
    mode: str, stuck: StuckSite, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stuck.mode = mode

    await asyncio.wait_for((await make_connector()).run_sync(), 20)

    pages = db.pages()
    assert pages[STUCK].indexing_status == ProgressStatus.FAILED.value
    assert pages[STUCK].reason == UNREACHABLE
    assert pages[FINE].indexing_status != ProgressStatus.FAILED.value
    assert pages[FINE].record_name == "Fine"
    assert stuck.attempts > 1, "the page was given up on without being retried"
    assert f"Gave up on {STUCK} after 0.3 seconds" in caplog.text


async def test_a_trickling_transfer_stops_once_it_is_given_up_on(
    stuck: StuckSite, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    stuck.mode = "trickle"

    await asyncio.wait_for((await make_connector()).run_sync(), 20)

    assert db.pages()[STUCK].indexing_status == ProgressStatus.FAILED.value
    # Each attempt's thread stops reading at its next byte after its deadline, not trickling on.
    for _ in range(100):
        if stuck.trickles_running == 0:
            break
        await asyncio.sleep(0.02)
    assert stuck.trickles_running == 0


async def test_the_next_sync_fetches_a_page_that_was_given_up_on(
    stuck: StuckSite, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    stuck.mode = "wedge"
    await asyncio.wait_for((await make_connector()).run_sync(), 20)
    assert db.pages()[STUCK].indexing_status == ProgressStatus.FAILED.value

    stuck.mode = None
    site.html(STUCK, "Stuck no more")
    await asyncio.wait_for((await make_connector()).run_sync(), 20)

    page = db.pages()[STUCK]
    assert page.indexing_status != ProgressStatus.FAILED.value
    assert page.record_name == "Stuck no more"

# pyright: ignore-file

"""
Web Connector – Fault Recovery Integration Tests
================================================

The site the connector crawls misbehaves the way real sites do, through the
``web-fixtures`` fault modes: rate limiting, server errors, a slow answer, and a
page cut off in transit. Each test adds pages, links them from the start page,
puts a fault on them, and runs the next crawl with the resync API.

What the connector does today, and so what these hold it to:
  * 429 and 503 are retried inside the fetch, honouring Retry-After up to five
    minutes. A site asking for longer has its page left for the next crawl.
  * Any other 5xx, a dropped connection, or a timeout sends the page to the
    crawl's retry queue, which tries twice more with a back-off before giving up.
  * A page it gives up on is kept as a record marked FAILED, never silently
    dropped, and the next crawl that can fetch it turns that record into the page.
  * A page cut short with nothing to show for it (a complete response that
    carries half a document) cannot be told apart from a real page. It is
    indexed as served, and the next crawl repairs it because the text changed.

Test cases:
  TC-FAULT-001 — Pages hit by rate limits, errors, a timeout and a dropped
                 connection are all indexed in the same crawl
  TC-FAULT-002 — A page that never loads is kept as FAILED, then recovered
  TC-FAULT-003 — A page served cut short is repaired by the next crawl
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import ProgressStatus
from connectors.web.conftest import EXPECTED_PAGES  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import (
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.storage_incremental import settle_record_baseline, wait_for_record_reindex
from helper.web_fixtures import WebFixtures, html_page
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("web-faults-test")

HOME_PATH = "site/docs/index.html"
# Past a slow page's wait, and past the Retry-After and back-off a crawl sits
# out between attempts.
FAULT_SYNC_TIMEOUT = 600


@dataclass(frozen=True)
class FaultyPage:
    title: str
    path: str
    marker: str
    fault: dict[str, Any]


def _page_text(page: FaultyPage) -> str:
    # The marker ends the page, so a copy cut anywhere short of the end lacks it.
    filler = "This paragraph pads the page so that half of it is a real cut. " * 20
    return html_page(page.title, f"{filler} Marker {page.marker}.")


def _publish(web_fixtures: WebFixtures, pages: list[FaultyPage]) -> None:
    """Serve the pages, link them from the start page, then arm their faults."""
    for page in pages:
        web_fixtures.put(page.path, _page_text(page), "text/html; charset=utf-8")
    links = "".join(
        f'<li><a href="{Path(page.path).name}">{page.title}</a></li>' for page in pages
    )
    home = web_fixtures.get(HOME_PATH)
    web_fixtures.put(HOME_PATH, home.replace("</ul>", f"{links}</ul>", 1), "text/html; charset=utf-8")
    for page in pages:
        web_fixtures.add_fault(page.path, **page.fault)


def _resync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    pipeshub_client.resync_connector(connector_id, full_sync=False)


async def _record(
    graph_provider: GraphProviderProtocol, connector_id: str, web_fixtures: WebFixtures, page: FaultyPage
) -> Any:
    return await graph_provider.get_record_by_external_id(connector_id, web_fixtures.url_for_connector(page.path))


async def _streamed_text(pipeshub_client: PipeshubClient, record: Any) -> str:
    return pipeshub_client.stream_record(record.id).content.decode("utf-8", errors="replace")


@pytest.mark.integration
@pytest.mark.web
@pytest.mark.asyncio(loop_scope="session")
class TestWebConnectorFaults:
    """The crawl survives a misbehaving site without losing or duplicating pages."""

    @pytest.mark.order(1)
    async def test_tc_fault_001_transient_faults_recover_within_one_crawl(
        self,
        web_connector: dict[str, Any],
        web_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FAULT-001: Every page behind a passing fault is indexed by the crawl that met it."""
        connector_id = web_connector["connector_id"]
        pages = [
            FaultyPage("Fixture Rate Limited Page", "site/docs/rate-limited.html", "fault-rate-limited",
                       {"status": 429, "retry_after": 2, "times": 2}),
            FaultyPage("Fixture Unavailable Page", "site/docs/unavailable.html", "fault-unavailable",
                       {"status": 503, "times": 1}),
            FaultyPage("Fixture Server Error Page", "site/docs/server-error.html", "fault-server-error",
                       {"status": 500, "times": 1}),
            FaultyPage("Fixture Dropped Connection Page", "site/docs/dropped.html", "fault-dropped",
                       {"truncate": True, "times": 1}),
            # Longer than the connector's 15-second request timeout.
            FaultyPage("Fixture Slow Page", "site/docs/slow.html", "fault-slow",
                       {"delay": 20, "times": 1}),
        ]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        _publish(web_fixtures, pages)
        _resync(pipeshub_client, connector_id)
        after_count = await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=FAULT_SYNC_TIMEOUT
        )

        for page in pages:
            assert web_fixtures.fault_hits(page.path) == page.fault["times"], (
                f"TC-FAULT-001: the fault on {page.path} answered "
                f"{web_fixtures.fault_hits(page.path)} of {page.fault['times']} requests, "
                "so the crawl never got past it"
            )
            record = await _record(graph_provider, connector_id, web_fixtures, page)
            assert record is not None, f"TC-FAULT-001: {page.path} was not indexed after {page.fault}"
            assert record.record_name == page.title, (
                f"TC-FAULT-001: {page.path} is {record.record_name!r}, not the page "
                f"({page.title!r}); a name taken from the URL means the crawl gave up on it"
            )
        assert after_count == before_count + len(pages), (
            f"TC-FAULT-001: expected {len(pages)} new records, count went {before_count} -> {after_count}"
        )
        dropped = await _record(graph_provider, connector_id, web_fixtures, pages[3])
        text = await _streamed_text(pipeshub_client, dropped)
        assert pages[3].marker in text, (
            f"TC-FAULT-001: the page whose first download was cut off streams an incomplete copy: {text[-300:]!r}"
        )

    @pytest.mark.order(2)
    async def test_tc_fault_002_page_that_never_loads_is_kept_then_recovered(
        self,
        web_connector: dict[str, Any],
        web_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FAULT-002: Giving up on a page leaves a FAILED record, which the page later replaces."""
        connector_id = web_connector["connector_id"]
        page = FaultyPage("Fixture Broken Page", "site/docs/broken.html", "fault-broken", {"status": 500})
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        _publish(web_fixtures, [page])
        _resync(pipeshub_client, connector_id)
        failed_count = await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=FAULT_SYNC_TIMEOUT
        )

        failed = await _record(graph_provider, connector_id, web_fixtures, page)
        assert failed is not None, (
            "TC-FAULT-002: a page the crawl could not fetch left no record; it was silently dropped"
        )
        # The record is written FAILED, then indexing may pick it up and fail it
        # again, so any status but COMPLETED is right; a page title is not.
        assert failed.indexing_status != ProgressStatus.COMPLETED.value, (
            "TC-FAULT-002: a page that never loaded is marked COMPLETED"
        )
        assert failed.record_name != page.title, (
            f"TC-FAULT-002: the unfetchable page is named {failed.record_name!r}, its real title"
        )
        assert web_fixtures.fault_hits(page.path) >= 3, (
            f"TC-FAULT-002: the page was requested {web_fixtures.fault_hits(page.path)} time(s); "
            "the crawl should try it three times before giving up"
        )
        assert failed_count == before_count + 1, (
            f"TC-FAULT-002: expected one record for the failed page, count went {before_count} -> {failed_count}"
        )

        web_fixtures.remove_fault(page.path)
        _resync(pipeshub_client, connector_id)

        async def _recovered() -> bool:
            record = await _record(graph_provider, connector_id, web_fixtures, page)
            return (
                record is not None
                and record.record_name == page.title
                and record.indexing_status == ProgressStatus.COMPLETED.value
            )

        await wait_until_graph_condition(
            connector_id, check=_recovered, timeout=FAULT_SYNC_TIMEOUT, poll_interval=10,
            description="the failed page to be crawled and indexed",
        )
        recovered = await _record(graph_provider, connector_id, web_fixtures, page)
        assert recovered.id == failed.id, (
            f"TC-FAULT-002: the recovered page is a new record ({recovered.id}), not the FAILED one ({failed.id})"
        )
        after_count = await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        assert after_count == failed_count, (
            f"TC-FAULT-002: recovering the page moved the count from {failed_count} to {after_count}"
        )

    @pytest.mark.order(3)
    async def test_tc_fault_003_page_served_cut_short_is_repaired_next_crawl(
        self,
        web_connector: dict[str, Any],
        web_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FAULT-003: A half page that looked whole is indexed as served, then replaced."""
        connector_id = web_connector["connector_id"]
        page = FaultyPage("Fixture Half Served Page", "site/docs/half.html", "fault-half",
                          {"partial": True, "times": 1})
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        _publish(web_fixtures, [page])
        _resync(pipeshub_client, connector_id)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=FAULT_SYNC_TIMEOUT)
        assert web_fixtures.fault_hits(page.path) == 1, "TC-FAULT-003: the crawl never requested the page"
        cut = await _record(graph_provider, connector_id, web_fixtures, page)
        assert cut is not None, "TC-FAULT-003: the half-served page was not indexed"
        assert page.marker not in await _streamed_text(pipeshub_client, cut), (
            "TC-FAULT-003: the page's end is in the stream, so the crawl did not store the half it was served"
        )

        _resync(pipeshub_client, connector_id)
        await wait_for_record_reindex(
            graph_provider, connector_id, page.title, cut.version, timeout=FAULT_SYNC_TIMEOUT
        )
        repaired = await _record(graph_provider, connector_id, web_fixtures, page)
        assert repaired is not None and repaired.id == cut.id, "TC-FAULT-003: the repaired page is a new record"
        text = await _streamed_text(pipeshub_client, cut)
        assert page.marker in text, f"TC-FAULT-003: the next crawl did not repair the page: {text[-300:]!r}"
        for name in EXPECTED_PAGES:
            assert await graph_provider.get_record_by_name(connector_id, name) is not None, (
                f"TC-FAULT-003: {name} went missing while other pages were failing"
            )

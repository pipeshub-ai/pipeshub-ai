"""The crawl never requests a loopback, private, link-local or cloud metadata address, whether it
is the start URL, a link, or a redirect hop, by any fetch strategy or by the browser.

intranet.test resolves to a private address (see conftest); every other fake host to a public one.
"""

from collections.abc import Callable

import pytest
from web_behaviour_fakes import START_URL, FakeRecordsDb, FakeWeb, MakeConnector, Page


STRATEGIES = ["aiohttp", "curl_cffi", "cloudscraper"]
METADATA = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
INTRANET = "http://intranet.test/admin"


def _requested(site: FakeWeb, url: str) -> bool:
    return any(requested == url for _method, requested in site.requests)


def _not_saved(db: FakeRecordsDb, *urls: str) -> bool:
    """No refused URL was stored as a page; at most as a failed or skipped one."""
    pages = db.pages()
    return START_URL in pages and all(url not in pages or pages[url].reason for url in urls)


@pytest.mark.parametrize("target", [METADATA, INTRANET])
@pytest.mark.parametrize("strategy", STRATEGIES)
async def test_a_redirect_to_an_internal_address_is_never_requested(
    strategy: str, target: str, site: FakeWeb, db: FakeRecordsDb,
    use_strategy: Callable[[str], None], make_connector: MakeConnector,
) -> None:
    use_strategy(strategy)
    site.html(START_URL, "Home", "/go")
    site.add("http://site.test/go", Page(status=302, location=target, content_type=None, head_status=405))
    site.html(target, "Secret")

    await (await make_connector(follow_external=True)).run_sync()

    assert not _requested(site, target)
    assert _not_saved(db, target)


async def test_a_link_to_an_internal_address_is_never_requested(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", INTRANET, METADATA)
    site.html(INTRANET, "Admin")
    site.html(METADATA, "Credentials")

    await (await make_connector(follow_external=True)).run_sync()

    assert not _requested(site, INTRANET)
    assert not _requested(site, METADATA)
    assert _not_saved(db, INTRANET, METADATA)


async def test_robust_mode_never_loads_a_redirect_to_an_internal_address(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", "/go")
    browser.add("http://site.test/go", Page(status=302, location=METADATA, content_type=None))
    browser.html(METADATA, "Credentials")

    await (await make_connector(use_headless_browser=True, follow_external=True)).run_sync()

    assert not _requested(browser, METADATA)
    assert _not_saved(db, METADATA)


@pytest.mark.parametrize("start", [METADATA, INTRANET, "http://localhost:8088/"])
async def test_an_internal_start_url_is_never_requested(
    start: str, site: FakeWeb, make_connector: MakeConnector,
) -> None:
    site.html(start, "Secret")

    await (await make_connector(start)).run_sync()

    assert not _requested(site, start)

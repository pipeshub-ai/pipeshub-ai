"""Redirects in a normal crawl are followed one hop at a time, each target checked (scope, robots.txt)
before it is requested, whether the site answers HEAD or not, and by each fetch strategy.

curl_cffi and cloudscraper are the fakes from web_behaviour_fakes, answered by the same fake site.
"""

from collections.abc import Callable

import pytest
from web_behaviour_fakes import START_URL, FakeRecordsDb, FakeWeb, MakeConnector, Page

STRATEGIES = ["aiohttp", "curl_cffi", "cloudscraper"]
SECRET = "http://site.test/private/secret"


def _robots(site: FakeWeb) -> None:
    site.add("http://site.test/robots.txt",
             Page(body=b"User-agent: *\nDisallow: /private/\n", content_type="text/plain"))


def _requests_to(site: FakeWeb, url: str) -> list[str]:
    return [method for method, requested in site.requests if requested == url]


def _served_by(site: FakeWeb, strategy: str, url: str) -> bool:
    """Every GET for ``url`` came from ``strategy``, and there was at least one."""
    clients = {via for via, method, requested in site.served if method == "GET" and requested == url}
    return clients == {strategy}


@pytest.mark.parametrize("strategy", STRATEGIES)
async def test_a_site_that_refuses_head_never_gets_a_request_for_a_disallowed_redirect(
    strategy: str, site: FakeWeb, db: FakeRecordsDb, use_strategy: Callable[[str], None],
    make_connector: MakeConnector,
) -> None:
    use_strategy(strategy)
    _robots(site)
    site.html(START_URL, "Home", "/go")
    site.add("http://site.test/go", Page(status=302, location="/private/secret", content_type=None, head_status=405))
    site.html(SECRET, "Secret")

    await (await make_connector()).run_sync()

    assert _served_by(site, strategy, "http://site.test/go")
    assert _requests_to(site, SECRET) == []
    assert set(db.pages()) == {START_URL}


@pytest.mark.parametrize("strategy", STRATEGIES)
async def test_a_redirect_only_get_sees_is_checked_before_it_is_followed(
    strategy: str, site: FakeWeb, db: FakeRecordsDb, use_strategy: Callable[[str], None],
    make_connector: MakeConnector,
) -> None:
    use_strategy(strategy)
    _robots(site)
    site.html(START_URL, "Home", "/go")
    site.add("http://site.test/go", Page(status=302, location="/private/secret", content_type=None, head_status=200))
    site.html(SECRET, "Secret")

    await (await make_connector()).run_sync()

    assert _requests_to(site, SECRET) == []
    assert set(db.pages()) == {START_URL}


@pytest.mark.parametrize("head", [None, 405], ids=["head-answers", "head-refused"])
@pytest.mark.parametrize("strategy", STRATEGIES)
async def test_an_allowed_chain_of_n_redirects_costs_n_plus_two_requests(
    strategy: str, head: int | None, site: FakeWeb, db: FakeRecordsDb,
    use_strategy: Callable[[str], None], make_connector: MakeConnector,
) -> None:
    use_strategy(strategy)
    chain = ["http://site.test/a", "http://site.test/b", "http://site.test/c"]
    site.html(START_URL, "Home", "/a")
    site.add(chain[0], Page(status=301, location="/b", content_type=None, head_status=head))
    site.add(chain[1], Page(status=302, location="//site.test/c", content_type=None, head_status=head))
    site.html(chain[2], "Landing")

    await (await make_connector()).run_sync()

    assert db.pages()["http://site.test/c"].record_name == "Landing"
    hops = [(method, url) for method, url in site.requests if url in chain]
    assert len(hops) == (len(chain) - 1) + 2  # N redirects + 2
    assert [url for method, url in hops if method == "GET"][-1] == chain[-1]


@pytest.mark.parametrize("strategy", STRATEGIES)
async def test_a_cookie_set_on_a_redirect_is_sent_on_the_next_hop(
    strategy: str, site: FakeWeb, db: FakeRecordsDb, use_strategy: Callable[[str], None],
    make_connector: MakeConnector,
) -> None:
    use_strategy(strategy)
    site.html(START_URL, "Home", "/login")
    site.add("http://site.test/login", Page(status=302, location="/members", content_type=None,
                                            headers={"Set-Cookie": "sid=abc; Path=/"}, head_status=405))
    site.html("http://site.test/members", "Members", requires_cookie="sid=abc")

    await (await make_connector()).run_sync()

    assert db.pages()["http://site.test/members"].record_name == "Members"
    assert _served_by(site, strategy, "http://site.test/members")  # no fallback to another client


async def test_a_cloudflare_challenge_that_ends_in_a_redirect_is_followed_by_the_same_scraper(
    site: FakeWeb, db: FakeRecordsDb, use_strategy: Callable[[str], None], make_connector: MakeConnector,
) -> None:
    use_strategy("cloudscraper")
    protected = "http://site.test/protected"
    site.html(START_URL, "Home", "/protected")
    site.html(protected, "Protected", cloudflare_challenge=True)

    await (await make_connector()).run_sync()

    assert db.pages()[protected].record_name == "Protected"
    assert _served_by(site, "cloudscraper", protected)  # no fallback to aiohttp


@pytest.mark.parametrize("chunked", [False, True], ids=["declared-size", "streamed"])
@pytest.mark.parametrize("strategy", STRATEGIES)
async def test_a_site_that_refuses_head_still_gets_the_size_limit_at_the_final_hop(
    strategy: str, chunked: bool, site: FakeWeb, db: FakeRecordsDb, use_strategy: Callable[[str], None],
    make_connector: MakeConnector,
) -> None:
    use_strategy(strategy)
    big = "http://site.test/files/big.pdf"
    site.html(START_URL, "Home", "/big.pdf")
    site.add("http://site.test/big.pdf", Page(status=302, location="/files/big.pdf", content_type=None, head_status=405))
    site.add(big, Page(body=b"x" * (2 * 1024 * 1024), content_type="application/pdf", head_status=405, chunked=chunked))

    await (await make_connector(max_size_mb=1)).run_sync()

    assert (db.pages()[big].reason or "").startswith("This file is larger than this connector's 1 MB size limit")

"""robots.txt: read once per site per crawl, and honoured unless the user turns it off (RFC 9309)."""

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    RecordingNotifications,
    VirtualClock,
)

ROBOTS = "http://site.test/robots.txt"


def _robots(site: FakeWeb, body: str, status: int = 200) -> None:
    site.add(ROBOTS, Page(status=status, body=body.encode(), content_type="text/plain"))


def _site(site: FakeWeb) -> None:
    site.html(START_URL, "Home", "/public", "/private/secret")
    site.html("http://site.test/public", "Public")
    site.html("http://site.test/private/secret", "Secret")


async def test_pages_robots_txt_disallows_are_skipped_and_the_summary_says_how_to_include_them(
    site: FakeWeb, db: FakeRecordsDb, notifications: RecordingNotifications, make_connector: MakeConnector
) -> None:
    _site(site)
    _robots(site, "User-agent: *\nDisallow: /private/\n")

    await (await make_connector()).run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/public"}
    assert site.gets("http://site.test/private/secret") == 0
    assert site.gets(ROBOTS) == 1
    assert (await notifications.delivered())[-1]["message"].endswith(
        "Skipped 1 pages that the site's robots.txt asks crawlers not to visit. "
        "To include them, turn off Respect robots.txt in the connector settings."
    )


async def test_rules_for_pipeshub_by_name_win_over_the_general_ones(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _site(site)
    _robots(site, "User-agent: PipesHub\nDisallow: /\n\nUser-agent: *\nAllow: /\n")

    await (await make_connector()).run_sync()

    assert db.pages() == {}
    assert site.fetched_urls() == set()


async def test_turning_the_setting_off_crawls_everything(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _site(site)
    _robots(site, "User-agent: *\nDisallow: /\n")

    await (await make_connector(respect_robots_txt=False)).run_sync()

    assert "http://site.test/private/secret" in db.pages()
    assert site.gets(ROBOTS) == 0


@pytest.mark.parametrize("status", [404, 403, 410])
async def test_a_missing_or_refused_robots_txt_allows_everything(
    status: int, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _site(site)
    _robots(site, "User-agent: *\nDisallow: /\n", status=status)

    await (await make_connector()).run_sync()

    assert "http://site.test/private/secret" in db.pages()


@pytest.mark.parametrize(
    "robots_page",
    [
        pytest.param(Page(status=503, body=b""), id="503"),
        pytest.param(Page(status=500, body=b""), id="500"),
        pytest.param(Page(hang_up=True), id="no-answer"),
    ],
)
async def test_an_unreadable_robots_txt_pauses_the_site_without_touching_stored_pages(
    robots_page: Page, site: FakeWeb, db: FakeRecordsDb, notifications: RecordingNotifications,
    make_connector: MakeConnector,
) -> None:
    _site(site)
    connector = await make_connector()
    await connector.run_sync()
    stored = {url: r.id for url, r in db.pages().items()}
    gets_before = site.gets(START_URL)

    site.add(ROBOTS, robots_page)
    await connector.run_sync()
    await connector.run_sync()

    assert site.gets(START_URL) == gets_before
    assert {url: r.id for url, r in db.pages().items()} == stored
    assert db.deleted == []
    assert (await notifications.delivered())[-1]["message"].endswith(
        "Couldn't read robots.txt for site.test, so it wasn't crawled this time. "
        "PipesHub will try again on the next sync."
    )

    site.remove(ROBOTS)
    await connector.run_sync()
    assert site.gets(START_URL) == gets_before + 1


async def test_robust_mode_honours_robots_txt_too(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _site(browser)
    _robots(browser, "User-agent: *\nDisallow: /private/\n")

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/public"}
    assert "http://site.test/private/secret" not in browser.browser_visits


async def test_each_site_s_own_robots_txt_applies_to_its_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "http://other.test/a", "/b")
    site.html("http://other.test/a", "Other A")
    site.html("http://site.test/b", "B")
    site.add("http://other.test/robots.txt", Page(body=b"User-agent: *\nDisallow: /\n", content_type="text/plain"))

    await (await make_connector(follow_external=True)).run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/b"}


@pytest.mark.parametrize(
    ("robots", "allowed", "blocked"),
    [
        pytest.param("Disallow: /\nAllow: /public\nAllow: /$", ["/public", "/public/a"], ["/other"], id="allow-inside-disallow-all"),
        pytest.param("Allow: /\nDisallow: /private/", ["/other"], ["/private/secret"], id="disallow-inside-allow-all"),
        pytest.param("Disallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php", ["/wp-admin/admin-ajax.php"],
                     ["/wp-admin/options"], id="longer-allow-wins"),
        pytest.param("Disallow: /page\nAllow: /page", ["/page"], [], id="allow-wins-a-tie"),
        pytest.param("Disallow: /*.pdf$", ["/files/a.pdf?download=1", "/files/a.pdfx"], ["/files/a.pdf"],
                     id="star-and-dollar"),
    ],
)
async def test_the_longest_matching_rule_decides_and_allow_wins_a_tie(
    robots: str, allowed: list[str], blocked: list[str],
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    paths = allowed + blocked
    site.html(START_URL, "Home", *paths)
    for path in paths:
        site.html(f"http://site.test{path}", path)
    _robots(site, "User-agent: *\n" + robots + "\n")

    await (await make_connector()).run_sync()

    for path in allowed:
        assert f"http://site.test{path}" in db.pages(), path
    for path in blocked:
        assert site.gets(f"http://site.test{path}") == 0, path


@pytest.mark.parametrize("status", [429, 503], ids=["429", "503"])
async def test_a_rate_limited_robots_txt_skips_the_site_at_once(
    status: int, site: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    _site(site)
    site.add(ROBOTS, Page(status=status, body=b"", headers={"Retry-After": "120"}))

    await (await make_connector()).run_sync()

    assert site.gets(ROBOTS) == 1
    assert clock.sleeps == []
    assert db.pages() == {}


@pytest.mark.parametrize(
    "robots",
    [
        pytest.param("User-agent: *\nDisallow: /\n\nUser-agent: PipesHub\n", id="empty-group"),
        pytest.param("User-agent: *\nDisallow: /\n\nUser-agent: PipesHub\nDisallow:\n", id="empty-disallow"),
    ],
)
async def test_a_group_naming_pipeshub_with_no_rules_allows_everything(
    robots: str, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _site(site)
    _robots(site, robots)

    await (await make_connector()).run_sync()

    assert "http://site.test/private/secret" in db.pages()


async def test_a_redirect_onto_a_disallowed_page_is_skipped_with_its_links(
    site: FakeWeb, db: FakeRecordsDb, notifications: RecordingNotifications, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/go")
    site.redirect("http://site.test/go", "/private/secret")
    site.html("http://site.test/private/secret", "Secret", "/leaked")
    site.html("http://site.test/leaked", "Leaked")
    _robots(site, "User-agent: *\nDisallow: /private/\n")

    await (await make_connector()).run_sync()

    assert set(db.pages()) == {START_URL}
    assert site.gets("http://site.test/leaked") == 0
    assert "Skipped 1 pages that the site's robots.txt" in (await notifications.delivered())[-1]["message"]


async def test_a_redirect_onto_another_site_follows_that_site_s_robots_txt(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/go")
    site.redirect("http://site.test/go", "http://other.test/secret")
    site.html("http://other.test/secret", "Secret")
    site.add("http://other.test/robots.txt", Page(body=b"User-agent: *\nDisallow: /secret\n", content_type="text/plain"))

    await (await make_connector(follow_external=True)).run_sync()

    assert site.gets("http://other.test/robots.txt") == 1
    assert "http://other.test/secret" not in db.pages()


@pytest.mark.parametrize("aborts", [False, True], ids=["browser-lands", "browser-aborts"])
async def test_robust_mode_never_downloads_a_file_a_redirect_lands_on_when_robots_txt_disallows_it(
    aborts: bool, browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/go")
    browser.redirect("http://site.test/go", "/private/report.pdf")
    browser.add("http://site.test/private/report.pdf",
                Page(body=b"%PDF-1.4 secret", content_type="application/pdf", browser_aborts=aborts))
    _robots(browser, "User-agent: *\nDisallow: /private/\n")

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert browser.gets("http://site.test/private/report.pdf") == 0
    assert set(db.pages()) == {START_URL}
    assert 240.0 not in clock.sleeps


async def test_a_single_page_that_redirects_onto_a_disallowed_page_is_not_stored(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.redirect("http://site.test/go", "/private/secret")
    site.html("http://site.test/private/secret", "Secret")
    _robots(site, "User-agent: *\nDisallow: /private/\n")

    await (await make_connector("http://site.test/go", crawl_type="single")).run_sync()

    assert db.pages() == {}


async def test_a_redirect_that_only_adds_a_trailing_slash_is_still_checked(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/secret")
    site.redirect("http://site.test/secret", "/secret/")
    site.html("http://site.test/secret/", "Secret")
    _robots(site, "User-agent: *\nDisallow: /secret/\n")

    await (await make_connector()).run_sync()

    assert set(db.pages()) == {START_URL}

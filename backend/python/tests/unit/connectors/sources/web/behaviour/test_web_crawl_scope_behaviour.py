"""Which pages a crawl visits and stores: domain, depth, page cap, URL filters and duplicates.

The crawler runs for real against small fake websites served over HTTP on a
Unix socket; see web_behaviour_fakes for what is faked and why.
"""

import pytest
from web_behaviour_fakes import START_URL, FakeRecordsDb, FakeWeb, MakeConnector, Page


def _page_urls(db: FakeRecordsDb) -> set[str]:
    return set(db.pages())


async def test_a_recursive_crawl_follows_same_site_links_and_stores_each_page_once(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/docs/", "/about")
    site.html("http://site.test/docs/", "Docs", "/docs/install", "/")
    site.html("http://site.test/docs/install", "Install", "/about")
    site.html("http://site.test/about", "About")

    connector = await make_connector()
    await connector.run_sync()

    assert _page_urls(db) == {
        "http://site.test/",
        "http://site.test/docs/",
        "http://site.test/docs/install",
        "http://site.test/about",
    }
    assert db.pages()["http://site.test/docs/install"].record_name == "Install"
    for url in ("http://site.test/", "http://site.test/about", "http://site.test/docs/install"):
        assert site.gets(url) == 1, url


async def test_links_to_other_sites_are_not_followed_unless_asked(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "http://other.test/elsewhere", "/local")
    site.html("http://site.test/local", "Local")
    site.html("http://other.test/elsewhere", "Elsewhere")

    await (await make_connector()).run_sync()

    assert site.gets("http://other.test/elsewhere") == 0
    assert _page_urls(db) == {"http://site.test/", "http://site.test/local"}


async def test_follow_external_crawls_linked_sites_too(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "http://other.test/elsewhere")
    site.html("http://other.test/elsewhere", "Elsewhere")

    await (await make_connector(follow_external=True)).run_sync()

    assert "http://other.test/elsewhere" in _page_urls(db)


async def test_depth_limit_stops_at_the_configured_number_of_hops(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/one")
    site.html("http://site.test/one", "One", "/two")
    site.html("http://site.test/two", "Two", "/three")
    site.html("http://site.test/three", "Three")

    await (await make_connector(depth=2)).run_sync()

    assert _page_urls(db) == {"http://site.test/", "http://site.test/one", "http://site.test/two"}
    assert site.gets("http://site.test/three") == 0


async def test_max_pages_caps_how_many_pages_are_fetched(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    links = [f"/p{i}" for i in range(10)]
    site.html(START_URL, "Home", *links)
    for link in links:
        site.html(f"http://site.test{link}", link)

    await (await make_connector(max_pages=4)).run_sync()

    assert len(site.fetched_urls()) == 4
    assert len(_page_urls(db)) == 4


async def test_single_page_crawl_stores_only_the_start_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/next")
    site.html("http://site.test/next", "Next")

    await (await make_connector(crawl_type="single")).run_sync()

    assert _page_urls(db) == {"http://site.test/"}
    assert site.gets("http://site.test/next") == 0


async def test_url_should_contain_keeps_matching_pages_and_the_start_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/blog/post", "/docs/guide")
    site.html("http://site.test/blog/post", "Post")
    site.html("http://site.test/docs/guide", "Guide")

    await (await make_connector(url_should_contain=["/DOCS/"])).run_sync()

    assert _page_urls(db) == {"http://site.test/", "http://site.test/docs/guide"}


async def test_restrict_to_start_path_never_climbs_above_the_start_folder(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    start = "http://site.test/docs"
    site.html(start, "Docs", "/docs/a", "/pricing", "/docs-old/x")
    site.html("http://site.test/docs/a", "A", "/")
    site.html("http://site.test/pricing", "Pricing")
    site.html("http://site.test/docs-old/x", "Old")
    site.html(START_URL, "Home")

    await (await make_connector(start, restrict_to_start_path=True, follow_external=True)).run_sync()

    assert _page_urls(db) == {"http://site.test/docs", "http://site.test/docs/a"}
    assert site.gets("http://site.test/pricing") == 0
    assert site.gets("http://site.test/docs-old/x") == 0
    assert site.gets(START_URL) == 0


async def test_a_redirect_that_leaves_the_site_is_not_stored(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/go", "/stay")
    site.redirect("http://site.test/go", "http://other.test/landing")
    site.html("http://other.test/landing", "Landing", "http://other.test/deeper")
    site.html("http://other.test/deeper", "Deeper")
    site.html("http://site.test/stay", "Stay")

    await (await make_connector()).run_sync()

    assert _page_urls(db) == {"http://site.test/", "http://site.test/stay"}
    assert site.gets("http://other.test/deeper") == 0


async def test_a_same_site_redirect_is_stored_under_the_page_it_lands_on(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/old-name")
    site.redirect("http://site.test/old-name", "/new-name", status=301)
    site.html("http://site.test/new-name", "New name")

    await (await make_connector()).run_sync()

    assert _page_urls(db) == {"http://site.test/", "http://site.test/new-name"}
    assert db.pages()["http://site.test/new-name"].external_record_id == "http://site.test/new-name/"


async def test_trailing_slash_and_fragment_variants_are_one_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/guide", "/guide/", "/guide#install", "#top")
    site.html("http://site.test/guide", "Guide")
    site.html("http://site.test/guide/", "Guide")

    await (await make_connector()).run_sync()

    guide_fetches = site.gets("http://site.test/guide") + site.gets("http://site.test/guide/")
    assert guide_fetches == 1
    guide_records = [r for r in db.pages().values() if r.external_record_id == "http://site.test/guide/"]
    assert len(guide_records) == 1
    assert site.gets(START_URL) == 1


async def test_a_page_linked_only_with_a_fragment_is_still_crawled(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/guide#install")
    site.html("http://site.test/guide", "Guide")

    await (await make_connector()).run_sync()

    assert "http://site.test/guide" in _page_urls(db)


async def test_query_string_order_does_not_make_a_second_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/list?a=1&b=2", "/list?b=2&a=1")
    site.html("http://site.test/list?a=1&b=2", "List")
    site.html("http://site.test/list?b=2&a=1", "List")

    await (await make_connector()).run_sync()

    assert len([u for u in _page_urls(db) if "/list" in u]) == 1


async def test_asset_links_are_not_crawled_as_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/logo.png", "/app.js", "/style.css", "mailto:hi@site.test", "/manual.pdf")
    site.add("http://site.test/manual.pdf", Page(body=b"%PDF-1.4 manual", content_type="application/pdf"))

    await (await make_connector()).run_sync()

    assert site.fetched_urls() == {"http://site.test/", "http://site.test/manual.pdf"}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The crawler never reads robots.txt, so pages a site asks crawlers to skip are "
        "fetched and indexed. Honouring robots.txt is a product decision."
    ),
)
async def test_pages_disallowed_by_robots_txt_are_not_crawled(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.add("http://site.test/robots.txt", Page(body=b"User-agent: *\nDisallow: /private/\n", content_type="text/plain"))
    site.html(START_URL, "Home", "/private/secret")
    site.html("http://site.test/private/secret", "Secret")

    await (await make_connector()).run_sync()

    assert site.gets("http://site.test/private/secret") == 0


async def test_a_page_reached_by_redirect_and_by_link_is_stored_once(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/old-name", "/new-name")
    site.redirect("http://site.test/old-name", "/new-name", status=301)
    site.html("http://site.test/new-name", "New name")

    await (await make_connector()).run_sync()

    assert _page_urls(db) == {"http://site.test/", "http://site.test/new-name"}
    assert len(site.storage_uploads) == 2
    assert site.gets("http://site.test/new-name") == 1


def _only(*extensions: str) -> dict:
    return {"sync": {"values": {"file_extensions": {"operator": "in", "value": list(extensions), "type": "multiselect"}}}}


@pytest.mark.parametrize("filters", [None, _only("pdf")], ids=["no-filter", "only-pdf"])
async def test_a_redirect_onto_a_file_is_stored_as_that_file(
    filters: dict | None, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/handbook", "/handbook.pdf")
    site.redirect("http://site.test/handbook", "/handbook.pdf")
    site.add("http://site.test/handbook.pdf", Page(body=b"%PDF-1.4 handbook", content_type="application/octet-stream"))

    await (await make_connector(filters=filters)).run_sync()

    handbook = db.pages()["http://site.test/handbook.pdf"]
    assert (handbook.mime_type, handbook.extension) == ("application/pdf", "pdf")
    assert [site.storage_docs[d] for d in site.storage_uploads].count(b"%PDF-1.4 handbook") == 1


async def test_relative_links_on_a_redirected_page_resolve_against_where_it_landed(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/old-name", "/docs/guide/")
    site.redirect("http://site.test/old-name", "/docs/guide/", status=301)
    site.html("http://site.test/docs/guide/", "Guide", "chapter")
    site.html("http://site.test/docs/guide/chapter", "Chapter")

    await (await make_connector()).run_sync()

    assert "http://site.test/docs/guide/chapter" in _page_urls(db)
    assert site.gets("http://site.test/chapter") == 0


@pytest.mark.parametrize("robust", [False, True], ids=["plain", "robust-mode"])
async def test_a_redirect_uses_one_page_of_the_page_limit(
    robust: bool, browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    """Like an http-to-https hop: the URL asked for and the URL landed on are one page."""
    browser.html(START_URL, "Home", "/moved")
    browser.redirect("http://site.test/moved", "/moved-here")
    browser.html("http://site.test/moved-here", "Moved here", "/other")
    browser.html("http://site.test/other", "Other")

    await (await make_connector(max_pages=3, use_headless_browser=robust)).run_sync()

    assert _page_urls(db) == {START_URL, "http://site.test/moved-here", "http://site.test/other"}


async def test_a_query_page_stored_before_keys_were_sorted_is_updated_not_duplicated(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    listing = "http://site.test/list?b=2&a=1"
    site.html(START_URL, "Home", "/list?b=2&a=1")
    site.html(listing, "List", text="old")
    connector = await make_connector()
    await connector.run_sync()
    [key] = [k for k in db.records if "/list" in k]
    stored = db.records.pop(key)
    stored.external_record_id = listing
    db.records[listing] = stored

    site.html(listing, "List", text="new")
    await connector.run_sync()

    list_records = [r for r in db.records.values() if "/list" in r.external_record_id]
    assert [(r.id, r.external_record_id) for r in list_records] == [(stored.id, "http://site.test/list?a=1&b=2")]

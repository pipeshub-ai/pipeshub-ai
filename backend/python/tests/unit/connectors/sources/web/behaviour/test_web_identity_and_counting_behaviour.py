"""Which URLs are one page, how many pages a connector keeps, and when unreached pages go.

Scenarios ID-01 to ID-18 of the web connector test plan. Max Pages is the number of pages the
connector stores, whatever number of syncs has run; pages a complete crawl no longer reaches are
removed after the second such crawl.
"""

import json

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeCheckpointStore,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    html_page,
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.sources.web.budget import MAX_RUN_HOURS_ENV

ARTICLE = "http://site.test/article/intro.html"


def _pages(db: FakeRecordsDb) -> set[str]:
    return {url for url, r in db.pages().items() if r.indexing_status != ProgressStatus.FAILED.value}


def _keys(db: FakeRecordsDb) -> set[str]:
    return {r.external_record_id for r in db.pages().values()}


def _last_run(checkpoints: FakeCheckpointStore) -> dict:
    (saved,) = checkpoints.sync_points.values()
    return json.loads(saved["lastRun"])


async def test_links_with_tracking_parameters_are_one_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/guide?utm_source=nav", "/guide?utm_source=footer#top", "/guide?gclid=9")
    site.html("http://site.test/guide", "Guide")

    await (await make_connector()).run_sync()

    assert _pages(db) == {START_URL, "http://site.test/guide"}
    assert site.gets("http://site.test/guide") == 1


async def test_ignored_parameters_fold_an_articles_variants_into_one_record(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    variants = [
        "/article/intro.html?brand=Acme&product=ED&language=en_US",
        "/article/intro.html?brand=Acme&product=ED&language=en_US&pills-nav=surveys",
        "/article/intro.html?brand=Acme&product=VOICE&language=en_US",
    ]
    site.html(START_URL, "Home", *variants)
    for variant in variants:  # the site only answers with its parameters
        site.html(f"http://site.test{variant}", "Intro")

    await (await make_connector(ignore_query_params=["brand", "product", "language", "pills-nav"])).run_sync()

    assert _keys(db) == {START_URL, ARTICLE}
    (article,) = [r for r in db.pages().values() if r.weburl != START_URL]
    assert article.weburl.startswith(ARTICLE + "?brand="), "the stored link still opens the page"
    assert sum(site.gets(f"http://site.test{v}") for v in variants) == 1, "one variant is fetched, not each"


async def test_without_ignored_parameters_each_variant_is_its_own_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/a?lang=en", "/a?lang=fr")
    site.html("http://site.test/a?lang=en", "A en")
    site.html("http://site.test/a?lang=fr", "A fr")

    await (await make_connector()).run_sync()

    assert _pages(db) == {START_URL, "http://site.test/a?lang=en", "http://site.test/a?lang=fr"}


async def test_a_canonical_naming_the_same_page_without_its_query_is_the_stored_url(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    canonical = '<link rel="canonical" href="http://site.test/doc">'
    site.html(START_URL, "Home", "/doc?view=print", "/doc?view=full")
    for view in ("print", "full"):
        site.add(f"http://site.test/doc?view={view}", Page(body=html_page("Doc", body=canonical)))

    await (await make_connector()).run_sync()

    assert _pages(db) == {START_URL, "http://site.test/doc"}


async def test_a_canonical_shared_by_many_pages_is_ignored(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    hub = '<link rel="canonical" href="http://site.test/article-hub.html">'
    articles = [f"/article/a{i}.html" for i in range(4)]
    site.html(START_URL, "Home", *articles)
    for path in articles:
        site.add(f"http://site.test{path}", Page(body=html_page(path, body=hub)))

    await (await make_connector()).run_sync()

    assert _pages(db) == {START_URL, *(f"http://site.test{p}" for p in articles)}


async def test_max_pages_holds_across_daily_syncs_of_a_site_that_shifts(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    """F3: the stored count used to grow past Max Pages as each sync reached a slightly different set."""
    connector = await make_connector(max_pages=4)
    for day in range(5):
        links = [f"/p{day + i}" for i in range(6)]
        site.html(START_URL, "Home", *links)
        for link in links:
            site.html(f"http://site.test{link}", link)

        await connector.run_sync()

        assert len(db.pages()) <= 4, f"day {day}: {sorted(db.pages())}"


async def test_unchanged_pages_count_toward_max_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    links = [f"/p{i}" for i in range(6)]
    site.html(START_URL, "Home", *links)
    for link in links:
        site.html(f"http://site.test{link}", link)
    connector = await make_connector(max_pages=3)
    await connector.run_sync()
    first = {url: r.id for url, r in db.pages().items()}

    await connector.run_sync()

    assert {url: r.id for url, r in db.pages().items()} == first


async def test_folder_records_do_not_count_toward_max_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    deep = ["/docs/guide/a", "/docs/guide/b", "/docs/api/c"]
    site.html(START_URL, "Home", *deep)
    for path in deep:
        site.html(f"http://site.test{path}", path)

    await (await make_connector(max_pages=4)).run_sync()

    assert len(db.pages()) == 4
    assert any(r.is_internal for r in db.records.values()), "the folder records are there too"


async def test_a_new_page_that_fails_is_stored_with_its_reason_within_max_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/broken", "/ok")
    site.add("http://site.test/broken", Page(status=500, body=b""))
    site.html("http://site.test/ok", "OK")

    await (await make_connector(max_pages=3)).run_sync()

    failed = db.pages()["http://site.test/broken"]
    assert failed.indexing_status == ProgressStatus.FAILED.value and failed.reason
    assert len(db.pages()) <= 3


async def test_failed_pages_do_not_push_the_count_past_max_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    broken = [f"/broken{i}" for i in range(5)]
    site.html(START_URL, "Home", *broken)
    for path in broken:
        site.add(f"http://site.test{path}", Page(status=500, body=b""))

    await (await make_connector(max_pages=3)).run_sync()

    assert len(db.pages()) <= 3


async def test_a_page_no_longer_linked_goes_after_the_second_complete_crawl(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
) -> None:
    pages = ["/a", "/b", "/c", "/old"]
    site.html(START_URL, "Home", *pages)
    for path in pages:
        site.html(f"http://site.test{path}", path)
    connector = await make_connector()
    await connector.run_sync()
    old = db.pages()["http://site.test/old"]

    site.html(START_URL, "Home", "/a", "/b", "/c")
    await connector.run_sync()
    assert "http://site.test/old" in db.pages(), "one crawl's miss is not enough"

    await connector.run_sync()

    assert "http://site.test/old" not in db.pages()
    assert db.deleted == [old.id]
    assert old.storage_document_id in site.storage_deletes
    assert _last_run(checkpoints)["pruned"] == 1


async def test_a_page_back_on_the_site_starts_counting_again(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    pages = ["/a", "/b", "/c", "/old"]
    site.html(START_URL, "Home", *pages)
    for path in pages:
        site.html(f"http://site.test{path}", path)
    connector = await make_connector()
    await connector.run_sync()

    for links in (["/a", "/b", "/c"], pages, ["/a", "/b", "/c"]):
        site.html(START_URL, "Home", *links)
        await connector.run_sync()

    assert "http://site.test/old" in db.pages()


async def test_a_partial_crawl_removes_nothing(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = ["/a", "/b", "/c", "/old"]
    site.html(START_URL, "Home", *pages)
    for path in pages:
        site.html(f"http://site.test{path}", path)
    connector = await make_connector()
    await connector.run_sync()

    # From now on every crawl runs out of time on a page that keeps asking it to wait.
    monkeypatch.setenv(MAX_RUN_HOURS_ENV, "0.005")
    site.html(START_URL, "Home", "/slow", "/a")
    site.add("http://site.test/slow", Page(status=429))
    for _ in range(3):
        await connector.run_sync()

    assert db.deleted == []


async def test_a_crawl_that_suddenly_misses_most_of_the_site_removes_nothing(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
) -> None:
    pages = [f"/p{i}" for i in range(6)]
    site.html(START_URL, "Home", *pages)
    for path in pages:
        site.html(f"http://site.test{path}", path)
    connector = await make_connector()
    await connector.run_sync()

    site.html(START_URL, "Home", "/p0")  # the site's navigation broke
    await connector.run_sync()
    await connector.run_sync()

    assert db.deleted == []
    assert _last_run(checkpoints)["prune_skipped"] is True


async def test_a_record_stored_under_a_tracking_url_is_replaced_by_the_clean_url(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/guide?utm_source=nav")
    site.html("http://site.test/guide", "Guide")
    connector = await make_connector()
    await connector.run_sync()
    stored = db.pages()["http://site.test/guide"]
    # As an older version of the connector keyed it: by the URL as linked.
    db.records.pop(stored.external_record_id)
    stored.external_record_id = "http://site.test/guide?utm_source=nav"
    stored.weburl = "http://site.test/guide?utm_source=nav"
    db.records[stored.external_record_id] = stored

    await connector.run_sync()
    assert "http://site.test/guide" in db.pages()

    # The link is cleaned before it is fetched, so the old record is never asked for again: it
    # goes like any page no longer reached, after the second complete crawl.
    await connector.run_sync()

    assert "http://site.test/guide?utm_source=nav" not in db.pages()
    assert stored.id in db.deleted


async def test_turning_on_ignored_parameters_clears_out_the_variants_even_when_they_are_most_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    variants = [f"/article/intro.html?lang={c}" for c in "abcdef"]
    site.html(START_URL, "Home", *variants)
    for variant in variants:
        site.html(f"http://site.test{variant}", "Intro")
    connector = await make_connector()
    await connector.run_sync()
    assert len(db.pages()) == 7

    connector.config_service.sync["ignore_query_params"] = ["lang"]  # read again at the next sync
    for _ in range(2):
        await connector.run_sync()

    assert _keys(db) == {START_URL, ARTICLE}



async def test_a_folder_that_turns_out_to_be_a_page_counts_toward_max_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    # /docs/a/ is stored as a folder for x.html first; when it is crawled as a page it is a new page.
    pages = ["/docs/a/x.html", "/docs/a/y.html", "/docs/a/", "/z1", "/z2"]
    site.html(START_URL, "Home", "/busy", *pages)
    for path in pages:
        site.html(f"http://site.test{path}", path)
    site.add("http://site.test/busy", Page(status=429, body=b""))

    await (await make_connector(max_pages=4)).run_sync()

    assert "http://site.test/docs/a/" in db.pages()
    assert len(db.pages()) <= 4


async def test_a_failed_start_page_removes_nothing_even_when_the_sitemap_reaches_the_rest(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    """Only the start-page guard stops this prune: the sitemap reaches most pages, so the half guard doesn't."""
    pages = [f"/p{i}" for i in range(6)]
    sitemap = "".join(f"<url><loc>http://site.test{p}</loc></url>" for p in pages)
    site.add("http://site.test/robots.txt", Page(
        body=b"User-agent: *\nAllow: /\nSitemap: http://site.test/sm.xml\n", content_type="text/plain",
    ))
    site.add("http://site.test/sm.xml", Page(
        body=f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sitemap}</urlset>'.encode(),
        content_type="application/xml",
    ))
    site.html(START_URL, "Home", *pages, "/old")
    for path in [*pages, "/old"]:
        site.html(f"http://site.test{path}", path)
    connector = await make_connector()
    await connector.run_sync()
    assert "http://site.test/old" in db.pages()

    site.add(START_URL, Page(status=500, body=b""))
    await connector.run_sync()
    await connector.run_sync()

    assert "http://site.test/old" in db.pages()
    assert db.deleted == []


async def test_a_stored_page_the_site_still_links_to_survives_max_pages_being_reached(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    for path in ("/a", "/b", "/new"):
        site.html(f"http://site.test{path}", path)
    site.html(START_URL, "Home", "/a", "/b")
    connector = await make_connector(max_pages=3)
    await connector.run_sync()
    assert {START_URL, "http://site.test/a", "http://site.test/b"} == set(db.pages())

    # A new page is linked first; the stored ones are still linked.
    site.html(START_URL, "Home", "/new", "/a", "/b")
    await connector.run_sync()
    await connector.run_sync()

    assert "http://site.test/b" in db.pages(), "a page the start page still links to was removed"


async def test_fetches_stay_within_three_times_max_pages(
    site: FakeWeb, make_connector: MakeConnector,
) -> None:
    missing = [f"/missing{i}" for i in range(20)]
    site.html(START_URL, "Home", *missing)
    for path in missing:
        site.add(f"http://site.test{path}", Page(status=404, body=b"gone"))

    await (await make_connector(max_pages=2)).run_sync()

    page_fetches = sum(site.gets(f"http://site.test{p}") for p in missing) + site.gets(START_URL)
    assert page_fetches <= 3 * 2, page_fetches


async def test_a_crawl_cut_off_by_the_fetch_cap_removes_no_stored_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    """Review finding: 40 new pages the store has no room for used up the fetch cap (3 × Max Pages)
    before the crawl got to p6..p9, and two such crawls pruned them although the site still links them."""
    stored = [f"/p{i}" for i in range(1, 10)]
    for page in stored:
        site.html(f"http://site.test{page}", page)
    site.html(START_URL, "Home", *stored)
    connector = await make_connector(max_pages=10)
    await connector.run_sync()
    assert len(db.pages()) == 10

    new = [f"/n{i}" for i in range(40)]
    for page in new:
        site.html(f"http://site.test{page}", page)
    site.html(START_URL, "Home", *stored[:5], *new, *stored[5:])
    await connector.run_sync()
    await connector.run_sync()

    assert {f"http://site.test{page}" for page in stored} <= set(db.pages())


async def test_a_single_page_max_pages_has_no_room_for_is_not_written_on_a_full_sync(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch,
) -> None:
    site.html(START_URL, "Home")
    connector = await make_connector(crawl_type="single")  # no sync point yet, so a full sync
    # Stands in for a store already holding Max Pages pages.
    monkeypatch.setattr(connector, "_admit_new_record", lambda record: False)

    await connector.run_sync()

    assert START_URL not in db.pages()
    assert not db.permission_updates, "a refused page was written through the permissions update"


async def test_pages_stored_beyond_max_pages_are_kept_while_the_site_still_has_them(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    """An upgrade: older versions capped each sync's visits, not stored pages, so a store can already
    hold more than Max Pages. The same happens when Max Pages is lowered. Nothing still on the site
    may be removed for that."""
    links = [f"/p{i}" for i in range(6)]
    site.html(START_URL, "Home", *links)
    for link in links:
        site.html(f"http://site.test{link}", link)
    await (await make_connector(max_pages=10)).run_sync()
    stored = set(db.pages())
    assert len(stored) == 7

    connector = await make_connector(max_pages=5)
    for _ in range(3):  # pruning needs two complete crawls in a row; give it three
        await connector.run_sync()

    assert set(db.pages()) == stored
    assert not db.deleted

"""Pages found through the site's sitemaps (scenarios DI-01, DI-02, DI-06 to DI-08, DI-20).

Before, a crawl only followed links, so pages a site lists in its sitemap but links to from
nowhere it reached (common on support and documentation sites) were never indexed.
"""

import json
from datetime import datetime, timezone

from web_behaviour_fakes import (
    START_URL,
    FakeCheckpointStore,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
)

ROBOTS = "http://site.test/robots.txt"
ORPHAN = "http://site.test/kb/orphan.html"
LINKED = "http://site.test/kb/linked.html"


def _urlset(*entries: tuple[str, str | None]) -> bytes:
    rows = "".join(
        f"<url><loc>{loc}</loc>{f'<lastmod>{mod}</lastmod>' if mod else ''}</url>" for loc, mod in entries
    )
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'.encode()


def _xml(site: FakeWeb, url: str, body: bytes) -> None:
    site.add(url, Page(body=body, content_type="application/xml"))


def _site(site: FakeWeb, *, robots_sitemap: str | None = "http://site.test/sm.xml") -> None:
    robots = "User-agent: *\nAllow: /\n" + (f"Sitemap: {robots_sitemap}\n" if robots_sitemap else "")
    site.add(ROBOTS, Page(body=robots.encode(), content_type="text/plain"))
    site.html(START_URL, "Home", "/kb/linked.html")
    site.html(LINKED, "Linked", "/kb/from-linked.html")
    site.html("http://site.test/kb/from-linked.html", "From linked")
    site.html(ORPHAN, "Orphan")


async def test_a_page_only_the_sitemap_lists_is_indexed(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
) -> None:
    _site(site)
    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, "2026-10-01"), (LINKED, None)))

    await (await make_connector()).run_sync()

    assert {ORPHAN, LINKED, "http://site.test/kb/from-linked.html"} <= set(db.pages())
    run = json.loads(next(iter(checkpoints.sync_points.values()))["lastRun"])
    assert (run["sitemap_urls"], run["sitemap_files_read"]) == (2, 1)


async def test_links_only_discovery_ignores_sitemaps(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _site(site)
    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, None)))

    await (await make_connector(discovery="links")).run_sync()

    assert ORPHAN not in db.pages()
    assert site.gets("http://site.test/sm.xml") == 0


async def test_sitemap_only_discovery_does_not_follow_links(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _site(site)
    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, None), (LINKED, None)))

    await (await make_connector(discovery="sitemap")).run_sync()

    assert {START_URL, ORPHAN, LINKED} <= set(db.pages())
    assert "http://site.test/kb/from-linked.html" not in db.pages()


async def test_without_a_sitemap_line_the_usual_address_is_tried(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _site(site, robots_sitemap=None)
    _xml(site, "http://site.test/sitemap.xml", _urlset((ORPHAN, None)))

    await (await make_connector()).run_sync()

    assert ORPHAN in db.pages()


async def test_an_index_with_a_gzipped_child_sitemap(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    import gzip

    _site(site)
    _xml(site, "http://site.test/sm.xml",
         b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
         b"<sitemap><loc>http://site.test/kb.xml.gz</loc></sitemap></sitemapindex>")
    site.add("http://site.test/kb.xml.gz", Page(body=gzip.compress(_urlset((ORPHAN, None))), content_type="application/gzip"))

    await (await make_connector()).run_sync()

    assert ORPHAN in db.pages()


async def test_sitemap_urls_outside_the_crawl_or_on_a_private_address_are_not_fetched(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _site(site)
    _xml(site, "http://site.test/sm.xml", _urlset(
        (ORPHAN, None), ("http://elsewhere.test/page", None), ("http://intranet.test/admin", None),
    ))

    await (await make_connector()).run_sync()

    assert ORPHAN in db.pages()
    fetched = site.fetched_urls()
    assert "http://elsewhere.test/page" not in fetched and "http://intranet.test/admin" not in fetched


async def test_a_sitemap_on_a_private_address_is_not_read(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _site(site, robots_sitemap="http://intranet.test/sm.xml")
    _xml(site, "http://intranet.test/sm.xml", _urlset((ORPHAN, None)))

    await (await make_connector()).run_sync()

    assert site.gets("http://intranet.test/sm.xml") == 0
    assert ORPHAN not in db.pages()


async def test_sitemap_only_skips_a_stored_page_its_lastmod_says_is_unchanged(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
) -> None:
    _site(site)
    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, "2020-01-01"), (LINKED, "2020-01-01")))
    connector = await make_connector(discovery="sitemap")
    await connector.run_sync()
    db.mark_indexed()
    fetched_once = site.gets(ORPHAN)

    await connector.run_sync()

    assert site.gets(ORPHAN) == fetched_once
    assert ORPHAN in db.pages(), "an unfetched page is still on the site, so it isn't pruned"
    run = json.loads(next(iter(checkpoints.sync_points.values()))["lastRun"])
    assert run["unchanged_by_lastmod"] == 2


async def test_sitemap_only_fetches_a_page_whose_lastmod_is_newer_than_the_stored_copy(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _site(site)
    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, "2020-01-01")))
    connector = await make_connector(discovery="sitemap")
    await connector.run_sync()
    fetched_once = site.gets(ORPHAN)

    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, "2999-01-01")))
    await connector.run_sync()

    assert site.gets(ORPHAN) == fetched_once + 1


async def test_sitemap_only_fetches_a_page_changed_later_on_the_day_of_its_date_only_lastmod(
    site: FakeWeb, make_connector: MakeConnector,
) -> None:
    """Review finding: ``<lastmod>`` with today's date read as midnight, before our crawl, so a page
    changed later that day was skipped on every later sync."""
    _site(site)
    today = datetime.now(timezone.utc).date().isoformat()
    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, today)))
    connector = await make_connector(discovery="sitemap")
    await connector.run_sync()
    fetched_once = site.gets(ORPHAN)

    await connector.run_sync()

    assert site.gets(ORPHAN) == fetched_once + 1


async def test_a_docs_site_under_a_path_has_its_sitemap_in_that_path(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    """Documentation published under a path (``/docs/``) usually has its sitemap there, not at the root."""
    site.add(ROBOTS, Page(body=b"User-agent: *\nAllow: /\n", content_type="text/plain"))
    site.html("http://site.test/docs/", "Docs home", "/docs/linked.html")
    site.html("http://site.test/docs/linked.html", "Linked")
    site.html("http://site.test/docs/orphan.html", "Orphan")
    _xml(site, "http://site.test/docs/sitemap.xml", _urlset(("http://site.test/docs/orphan.html", None)))

    await (await make_connector("http://site.test/docs/")).run_sync()

    assert "http://site.test/docs/orphan.html" in db.pages()


async def test_a_sitemap_does_not_crowd_out_the_section_the_start_url_points_at(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    """Found on a real site: started at /learning/ with Max Pages 20, 1 of the 20 pages stored was under /learning/."""
    start = "http://site.test/learning/"
    site.add(ROBOTS, Page(body=b"User-agent: *\nAllow: /\nSitemap: http://site.test/sm.xml\n", content_type="text/plain"))
    site.html(start, "Learning", "/learning/a.html", "/learning/b.html")
    site.html("http://site.test/learning/a.html", "A")
    site.html("http://site.test/learning/b.html", "B")
    news = [f"http://site.test/news/{i}.html" for i in range(5)]
    for url in news:
        site.html(url, "News")
    _xml(site, "http://site.test/sm.xml", _urlset(*((u, "2026-10-01") for u in news)))

    await (await make_connector(start, max_pages=3)).run_sync()

    assert {start, "http://site.test/learning/a.html", "http://site.test/learning/b.html"} == set(db.pages())


async def test_sitemap_only_fetches_a_page_stored_so_far_only_as_the_folder_of_its_children(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    folder = "http://site.test/kb/"
    _site(site)
    site.html(folder, "Knowledge base")
    _xml(site, "http://site.test/sm.xml", _urlset((ORPHAN, "2026-10-01"), (folder, "2020-01-01")))
    # Room for the start page and the orphan only: /kb/ is then just the folder holding the orphan.
    await (await make_connector(discovery="sitemap", max_pages=2)).run_sync()
    assert db.records[folder].is_internal

    await (await make_connector(discovery="sitemap", max_pages=10)).run_sync()

    assert folder in db.pages(), "the folder's own write time is not the page's"

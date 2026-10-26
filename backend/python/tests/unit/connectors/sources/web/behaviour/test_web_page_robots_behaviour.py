"""Per-page robots directives: noindex and nofollow (scenarios DI-14, DI-15)."""

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    html_page,
)

PAGE = "http://site.test/page"
CHILD = "http://site.test/child"


def _meta(content: str, name: str = "robots") -> bytes:
    return html_page("Page", "/child", body=f'<meta name="{name}" content="{content}">')


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(Page(body=_meta("noindex")), id="meta"),
        pytest.param(Page(body=_meta("NOINDEX, follow", name="PipesHub")), id="meta-for-us"),
        pytest.param(Page(body=html_page("Page", "/child"), headers={"X-Robots-Tag": "noindex"}), id="header"),
        pytest.param(Page(body=html_page("Page", "/child"), headers={"X-Robots-Tag": "pipeshub: noindex"}), id="header-for-us"),
    ],
)
async def test_a_noindex_page_is_not_stored_but_its_links_are_followed(
    page: Page, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/page")
    site.add(PAGE, page)
    site.html(CHILD, "Child")

    await (await make_connector()).run_sync()

    assert PAGE not in db.pages()
    assert CHILD in db.pages()


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(Page(body=_meta("nofollow")), id="meta"),
        pytest.param(Page(body=html_page("Page", "/child"), headers={"X-Robots-Tag": "nofollow"}), id="header"),
    ],
)
async def test_a_nofollow_page_is_stored_but_its_links_are_not_followed(
    page: Page, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/page")
    site.add(PAGE, page)
    site.html(CHILD, "Child")

    await (await make_connector()).run_sync()

    assert PAGE in db.pages()
    assert site.gets(CHILD) == 0


async def test_none_means_noindex_and_nofollow(site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector) -> None:
    site.html(START_URL, "Home", "/page")
    site.add(PAGE, Page(body=_meta("none")))
    site.html(CHILD, "Child")

    await (await make_connector()).run_sync()

    assert PAGE not in db.pages() and site.gets(CHILD) == 0


async def test_a_directive_for_another_crawler_is_ignored(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/page")
    site.add(PAGE, Page(body=html_page("Page"), headers={"X-Robots-Tag": "otherbot: noindex"}))

    await (await make_connector()).run_sync()

    assert PAGE in db.pages()


async def test_a_page_that_turns_noindex_is_removed(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/page")
    site.html(PAGE, "Page")
    connector = await make_connector()
    await connector.run_sync()
    stored = db.pages()[PAGE]

    site.add(PAGE, Page(body=_meta("noindex")))
    await connector.run_sync()

    assert PAGE not in db.pages() and stored.id in db.deleted


async def test_a_nofollow_link_is_not_followed(site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector) -> None:
    site.add(START_URL, Page(body=html_page("Home", "/page", body='<a rel="nofollow" href="/child">c</a>')))
    site.html(PAGE, "Page")
    site.html(CHILD, "Child")

    await (await make_connector()).run_sync()

    assert PAGE in db.pages() and site.gets(CHILD) == 0


async def test_with_robots_txt_not_respected_page_directives_are_ignored_too(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/page")
    site.add(PAGE, Page(body=_meta("noindex, nofollow")))
    site.html(CHILD, "Child")

    await (await make_connector(respect_robots_txt=False)).run_sync()

    assert {PAGE, CHILD} <= set(db.pages())

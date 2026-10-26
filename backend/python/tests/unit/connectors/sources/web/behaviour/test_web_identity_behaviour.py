"""Who the crawler says it is (scenarios DI-16 to DI-18, FR-06).

A new connector crawls as PipesHub, which is also the name its robots.txt groups match. A
connector set up before the setting existed keeps crawling as a browser, as it did.
"""

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    RecordingNotifications,
)

from app.config.constants.arangodb import ProgressStatus

PAGE = "http://site.test/page"


async def test_a_new_connector_crawls_as_pipeshub(site: FakeWeb, make_connector: MakeConnector) -> None:
    site.html(START_URL, "Home", "/page")
    site.html(PAGE, "Page")

    await (await make_connector(browser_like_identity=False)).run_sync()

    page_agents = {ua for (method, url), ua in zip(site.requests, site.user_agents) if url == PAGE}
    assert page_agents and all("PipesHub/" in ua for ua in page_agents)


async def test_without_a_stored_value_a_connector_crawls_with_the_settings_default_pipeshub(
    site: FakeWeb, make_connector: MakeConnector,
) -> None:
    # Connectors from before the setting get theirs stored at startup (web_browser_identity_migration),
    # so a missing value means what the settings form shows: off.
    site.html(START_URL, "Home", "/page")
    site.html(PAGE, "Page")

    await (await make_connector(browser_like_identity=None)).run_sync()

    page_agents = {ua for (method, url), ua in zip(site.requests, site.user_agents) if url == PAGE}
    assert page_agents and all("PipesHub/" in ua for ua in page_agents)


async def test_as_pipeshub_a_refusal_is_the_sites_answer(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", "/page")
    site.add(PAGE, Page(status=403, body=b"forbidden"))

    await (await make_connector(browser_like_identity=False)).run_sync()

    assert site.gets(PAGE) == 1, "no other disguise to try"
    assert PAGE not in site.browser_visits
    failed = db.pages()[PAGE]
    assert failed.indexing_status == ProgressStatus.FAILED.value
    assert "Identify as a web browser" in (failed.reason or "")


async def test_a_site_refusing_most_pages_gets_a_hint(
    site: FakeWeb, make_connector: MakeConnector, notifications: RecordingNotifications,
) -> None:
    pages = [f"/p{i}" for i in range(4)]
    site.html(START_URL, "Home", *pages)
    for path in pages:
        site.add(f"http://site.test{path}", Page(status=403, body=b""))

    await (await make_connector(browser_like_identity=False)).run_sync()

    assert "turn on Identify as a web browser" in (await notifications.delivered())[-1]["message"]


@pytest.mark.parametrize("browser_like", [True, False])
async def test_robots_txt_groups_for_pipeshub_apply_either_way(
    browser_like: bool, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    site.add("http://site.test/robots.txt", Page(
        body=b"User-agent: *\nAllow: /\n\nUser-agent: PipesHub\nDisallow: /page\n", content_type="text/plain",
    ))
    site.html(START_URL, "Home", "/page")
    site.html(PAGE, "Page")

    await (await make_connector(browser_like_identity=browser_like)).run_sync()

    assert site.gets(PAGE) == 0

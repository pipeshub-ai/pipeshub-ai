"""Opening a crawled page later, and how the connector's settings are applied.

A stored page is served from our storage copy, not re-fetched from the site; a
site error while fetching live is never passed through as our own status.
"""

import pytest
from fastapi import HTTPException
from web_behaviour_fakes import START_URL, FakeRecordsDb, FakeWeb, MakeConnector, Page

from app.config.constants.arangodb import MimeTypes
from app.models.entities import FileRecord, RecordType
from app.models.permission import EntityType

GUIDE = "http://site.test/guide"


async def _body(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


def _legacy_record(url: str) -> FileRecord:
    return FileRecord(
        id="legacy-1", org_id="org-1", record_name="Guide", record_type=RecordType.FILE,
        external_record_id=url, version=0, origin="CONNECTOR", connector_name="WEB",
        connector_id="web-1", weburl=url, is_file=True, mime_type=MimeTypes.HTML.value,
    )


async def test_opening_a_page_serves_the_stored_copy_without_refetching(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/guide")
    site.html(GUIDE, "Guide", text="Version one")
    connector = await make_connector()
    await connector.run_sync()
    site.html(GUIDE, "Guide", text="Version two")
    gets_before = site.gets(GUIDE)

    response = await connector.stream_record(db.pages()[GUIDE])

    body = await _body(response)
    assert b"Version one" in body
    assert site.gets(GUIDE) == gets_before


async def test_a_page_without_a_stored_copy_is_fetched_live_and_cleaned(
    site: FakeWeb, make_connector: MakeConnector
) -> None:
    site.add(GUIDE, Page(body=b"<html><body><p>Live text</p><script>track()</script></body></html>"))
    connector = await make_connector(GUIDE, crawl_type="single")

    body = await _body(await connector.stream_record(_legacy_record(GUIDE)))

    assert b"Live text" in body and b"track()" not in body


@pytest.mark.parametrize("site_status", [401, 999, 503])
async def test_a_site_error_while_fetching_live_is_not_passed_through_as_ours(
    site_status: int, site: FakeWeb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    connector = await make_connector()
    site.add(GUIDE, Page(status=site_status, body=b"nope"))

    with pytest.raises(HTTPException) as caught:
        await connector.stream_record(_legacy_record(GUIDE))

    assert caught.value.status_code != 401
    assert 400 <= caught.value.status_code < 600


async def test_a_record_with_nothing_to_open_is_reported_as_not_found(
    site: FakeWeb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    connector = await make_connector()
    record = _legacy_record(GUIDE)
    record.weburl = None

    with pytest.raises(HTTPException) as caught:
        await connector.stream_record(record)

    assert caught.value.status_code == 404


async def test_a_signed_link_comes_from_storage_and_falls_back_to_the_page_url(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    connector = await make_connector(crawl_type="single")
    await connector.run_sync()
    record = db.pages()[START_URL]

    assert await connector.get_signed_url(record) == f"http://storage.test/signed/{record.storage_document_id}"
    site.storage_down = True
    assert await connector.get_signed_url(record) == START_URL


async def test_the_website_url_cannot_be_changed_after_setup(
    site: FakeWeb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    connector = await make_connector()
    connector.config_service.sync["url"] = "http://elsewhere.test/"

    with pytest.raises(ValueError, match="Cannot change URL"):
        await connector.run_sync()


async def test_changed_crawl_settings_apply_on_the_next_sync(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/a")
    site.html("http://site.test/a", "A")
    connector = await make_connector(crawl_type="single")
    await connector.run_sync()
    assert set(db.pages()) == {START_URL}

    connector.config_service.sync["type"] = "recursive"
    await connector.run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/a"}


async def test_a_team_connector_shares_pages_with_the_whole_organisation(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    await (await make_connector(crawl_type="single", scope="team")).run_sync()

    group, permissions = db.record_groups[-1]
    assert group.web_url == START_URL
    assert [(p.entity_type, p.external_id) for p in permissions] == [(EntityType.ORG, "org-1")]


async def test_a_personal_connector_shares_pages_only_with_its_creator(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    await (await make_connector(crawl_type="single", scope="personal")).run_sync()

    _, permissions = db.record_groups[-1]
    assert [(p.entity_type, p.email) for p in permissions] == [(EntityType.USER, "owner@example.com")]


async def test_a_connector_without_a_website_url_does_not_start(
    site: FakeWeb, make_connector: MakeConnector
) -> None:
    await make_connector("", expect_init=False)


async def test_out_of_range_limits_are_clamped_to_what_the_form_allows(
    site: FakeWeb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    connector = await make_connector(depth=50, max_pages=50000, max_size_mb=500)

    assert (connector.max_depth, connector.max_pages, connector.max_size_mb) == (10, 10000, 100)

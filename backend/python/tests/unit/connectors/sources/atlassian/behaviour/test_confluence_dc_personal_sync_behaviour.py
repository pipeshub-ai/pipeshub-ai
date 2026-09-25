"""Confluence Data Center (personal) sync, driven end to end over a fake REST API.

The connector, its Confluence client and its request builder are all real; the
Confluence server is an in-memory stub and our databases are in-memory fakes.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    FakeConfigService,
    FakeRecordsDb,
    json_response,
)
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.constants.arangodb import ProgressStatus
from app.connectors.sources.atlassian.confluence_datacenter_personal.connector import (
    ConfluenceDataCenterPersonalConnector,
)
from app.models.entities import (
    CommentRecord,
    FileRecord,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, PermissionType
from app.sources.client.confluence.confluence import ConfluenceRESTClientViaToken
from app.sources.external.confluence.confluence import ConfluenceDataSource

CONNECTOR_ID = "conf-dc-personal-1"
BASE = "https://confluence.example.com"
API = "/rest/api"
FAKE_PAT = "fake-pat-for-tests"


def space(key: str, sid: int) -> dict[str, Any]:
    return {
        "id": sid,
        "key": key,
        "name": f"{key} space",
        "history": {"createdDate": "2024-01-01T00:00:00.000Z"},
        "_links": {"webui": f"/display/{key}"},
    }


def content(
    cid: str,
    space_key: str = "ENG",
    space_id: int = 10,
    version: int = 1,
    when: str = "2024-05-01T10:00:00.000Z",
    ancestors: Optional[list[dict[str, Any]]] = None,
    attachments: Optional[list[dict[str, Any]]] = None,
    attachment_total: Optional[int] = None,
    ctype: str = "page",
) -> dict[str, Any]:
    atts = attachments or []
    return {
        "id": cid,
        "type": ctype,
        "title": f"Title {cid}",
        "space": {"id": space_id, "key": space_key},
        "history": {
            "createdDate": "2024-01-01T00:00:00.000Z",
            "lastUpdated": {"when": when, "number": version},
        },
        "ancestors": ancestors or [],
        "children": {"attachment": {"results": atts, "size": attachment_total if attachment_total is not None else len(atts)}},
        "_links": {"webui": f"/pages/viewpage.action?pageId={cid}"},
    }


def attachment(aid: str, title: str = "report.pdf", media: str = "application/pdf") -> dict[str, Any]:
    return {
        "id": aid,
        "title": title,
        "version": {"number": 1, "when": "2024-05-01T10:00:00.000Z"},
        "extensions": {"mediaType": media, "fileSize": 2048},
        "_links": {"webui": f"/download/attachments/{aid}"},
    }


def listing(results: list[dict[str, Any]], next_start: Optional[int] = None) -> dict[str, Any]:
    links: dict[str, Any] = {"base": BASE, "context": ""}
    if next_start is not None:
        links["next"] = f"{API}/content/search?limit=50&start={next_start}"
    return {"results": results, "size": len(results), "_links": links}


class ContentSearch:
    """Answers ``/content/search`` per (type, space, start) and remembers each CQL."""

    def __init__(self) -> None:
        self.pages: dict[tuple[str, str, int], object] = {}
        self.cql: list[str] = []

    def add(self, ctype: str, space_key: str, start: int, response: object) -> None:
        self.pages[(ctype, space_key, start)] = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        cql = q["cql"]
        self.cql.append(cql)
        ctype = "page" if cql.startswith("type=page") else "blogpost"
        space_key = cql.split("space='")[1].split("'")[0]
        response = self.pages.get((ctype, space_key, int(q.get("start", 0))), listing([]))
        return response if isinstance(response, httpx.Response) else json_response(response)


def stub_spaces(api: AtlassianApiStub, *pages_of_spaces: dict[str, Any]) -> None:
    api.on("GET", f"{API}/space", list(pages_of_spaces))


def space_page(spaces: list[dict[str, Any]], next_start: Optional[int] = None) -> dict[str, Any]:
    links: dict[str, Any] = {"base": BASE}
    if next_start is not None:
        links["next"] = f"{API}/space?limit=25&start={next_start}"
    return {"results": spaces, "_links": links}


async def make_connector(
    api: AtlassianApiStub,
    db: FakeRecordsDb,
    checkpoints: FakeCheckpointStore,
    filters: Optional[dict[str, Any]] = None,
) -> ConfluenceDataCenterPersonalConnector:
    config = {
        "auth": {"authType": "API_TOKEN", "baseUrl": f"{BASE}/", "apiToken": FAKE_PAT},
        "filters": filters or {},
    }
    connector = ConfluenceDataCenterPersonalConnector(
        logging.getLogger("test.confluence_dc_personal"),
        db,
        checkpoints,
        FakeConfigService(CONNECTOR_ID, config),
        CONNECTOR_ID,
        "personal",
        "creator-user-1",
    )
    assert await connector.init() is True
    api.on_suffix("GET", "/child/comment", {"results": [], "_links": {"base": BASE}})
    api.on_suffix("GET", "/child/attachment", {"results": [], "_links": {"base": BASE}})
    api.install(connector.external_client.get_client())
    return connector


@pytest.fixture
def search(atlassian_api: AtlassianApiStub) -> ContentSearch:
    handler = ContentSearch()
    atlassian_api.on("GET", f"{API}/content/search", handler)
    return handler


def saved(db: FakeRecordsDb, record_type: RecordType) -> dict[str, Any]:
    return {k: r for k, r in db.records.items() if r.record_type == record_type}


class TestTheStubIsTheRealClientStack:
    async def test_connector_talks_through_real_client_and_request_builder(
        self, atlassian_api, records_db, checkpoints
    ) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        assert type(connector.data_source) is ConfluenceDataSource
        assert type(connector.external_client.get_client()) is ConfluenceRESTClientViaToken
        assert isinstance(connector.external_client.get_client().client, httpx.AsyncClient)

        atlassian_api.on("GET", f"{API}/space", space_page([space("ENG", 10)]))
        assert await connector.test_connection_and_access() is True

        request = atlassian_api.requests[-1]
        assert str(request.url).startswith(f"{BASE}{API}/space?")
        assert request.headers["Authorization"] == f"Bearer {FAKE_PAT}"

    async def test_connection_check_reports_a_rejected_token(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/space", json_response({"message": "Unauthorized"}, status=401))
        assert await connector.test_connection_and_access() is False

    async def test_init_fails_cleanly_without_a_token(self, records_db, checkpoints) -> None:
        config = {"auth": {"authType": "API_TOKEN", "baseUrl": BASE, "apiToken": ""}}
        connector = ConfluenceDataCenterPersonalConnector(
            logging.getLogger("t"), records_db, checkpoints, FakeConfigService(CONNECTOR_ID, config),
            CONNECTOR_ID, "personal", "creator-user-1",
        )
        assert await connector.init() is False
        with pytest.raises(HTTPException):
            await connector._get_fresh_datasource()


class TestFirstFullSync:
    async def test_every_page_of_spaces_and_content_is_synced_and_checkpointed(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(
            atlassian_api,
            space_page([space("ENG", 10), space("OPS", 20)], next_start=2),
            space_page([space("HR", 30)]),
        )
        search.add("page", "ENG", 0, listing([content("p1"), content("p2")], next_start=2))
        search.add("page", "ENG", 2, listing([content("p3")]))
        search.add("blogpost", "OPS", 0, listing([content("b1", "OPS", 20, ctype="blogpost")]))
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        space_requests = atlassian_api.calls("GET", f"{API}/space")
        listing_starts = [AtlassianApiStub.query(r).get("start") for r in space_requests if "homepage" not in r.url.query.decode()]
        assert listing_starts == ["0", "2"]
        assert set(records_db.record_groups) == {"10", "20", "30"}

        assert set(saved(records_db, RecordType.CONFLUENCE_PAGE)) == {"p1", "p2", "p3"}
        assert set(saved(records_db, RecordType.CONFLUENCE_BLOGPOST)) == {"b1"}
        first_cql = next(c for c in search.cql if "space='ENG'" in c and c.startswith("type=page"))
        assert "lastModified >" not in first_cql
        assert first_cql.endswith("order by lastModified asc")

        pages_checkpoint = checkpoints.values_for("confluence_pages/ENG")
        assert pages_checkpoint and pages_checkpoint["last_sync_time"].endswith("Z")
        assert checkpoints.values_for("confluence_blogposts/OPS")
        assert checkpoints.values_for("confluence_pages/HR") is None, "nothing synced, so no checkpoint"
        assert atlassian_api.unrouted == []

    async def test_only_the_creator_group_is_granted_access(self, atlassian_api, records_db, checkpoints, search) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")]))
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        (group, members), = records_db.user_groups
        assert [m.email for m in members] == ["owner@example.com"]
        (grant,) = records_db.record_group_permissions["10"]
        assert grant.entity_type == EntityType.GROUP
        assert grant.type == PermissionType.READ
        assert grant.external_id == group.source_user_group_id == f"internal-{CONNECTOR_ID}"
        assert records_db.record_permissions["p1"] == [], "pages inherit the space grant rather than carrying their own"

    async def test_without_a_known_creator_nobody_is_granted_access(self, atlassian_api, checkpoints, search) -> None:
        db = FakeRecordsDb(creator_email=None)
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        connector = await make_connector(atlassian_api, db, checkpoints)

        await connector.run_sync()

        assert db.user_groups == []
        assert db.record_group_permissions["10"] == []

    async def test_next_link_without_offset_continues_while_pages_are_full(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        next_without_offset = f"{API}/content/search?limit=50"
        full_page = {"results": [content(f"p{i}") for i in range(50)], "_links": {"base": BASE, "next": next_without_offset}}
        search.add("page", "ENG", 0, full_page)
        search.add("page", "ENG", 50, listing([content("p50")]))
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        assert len(saved(records_db, RecordType.CONFLUENCE_PAGE)) == 51


class TestIncrementalSync:
    async def test_second_run_asks_only_for_recent_changes_and_updates_records_in_place(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1", version=1), content("p2", version=1)]))
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        await connector.run_sync()
        first = {k: (r.id, r.version) for k, r in records_db.records.items()}
        checkpoint = checkpoints.values_for("confluence_pages/ENG")["last_sync_time"]

        moved = content("p1", version=2, ancestors=[{"id": "p2", "type": "page"}])
        search.add("page", "ENG", 0, listing([moved]))
        search.cql.clear()
        await connector.run_sync()

        cql = next(c for c in search.cql if c.startswith("type=page"))
        since = datetime.strptime(checkpoint, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc) - timedelta(hours=24)
        assert f'lastModified > "{since.strftime("%Y-%m-%d %H:%M")}"' in cql

        assert len(saved(records_db, RecordType.CONFLUENCE_PAGE)) == 2, "no duplicates"
        p1 = records_db.records["p1"]
        assert p1.id == first["p1"][0], "same record, updated in place"
        assert p1.version == first["p1"][1] + 1
        assert p1.external_revision_id == "2"
        assert p1.parent_external_record_id == "p2"
        assert p1.parent_record_type == RecordType.CONFLUENCE_PAGE

    async def test_an_unchanged_page_seen_again_keeps_its_version(self, atlassian_api, records_db, checkpoints, search) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1", version=3)]))
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        await connector.run_sync()
        before = (records_db.records["p1"].id, records_db.records["p1"].version)

        await connector.run_sync()

        assert (records_db.records["p1"].id, records_db.records["p1"].version) == before

    async def test_saved_date_filter_and_checkpoint_combine_to_the_later_one(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        filters = {"sync": {"values": {"modified": {"operator": "is_after", "type": "datetime", "value": {"start": 1577836800000}}}}}
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")]))
        connector = await make_connector(atlassian_api, records_db, checkpoints, filters=filters)
        await connector.run_sync()
        assert any('lastModified > "2019-12-31' in c for c in search.cql), search.cql

        search.cql.clear()
        await connector.run_sync()

        page_cql = next(c for c in search.cql if c.startswith("type=page"))
        assert '"2019-12-31' not in page_cql, "the newer checkpoint wins over the older filter date"


class TestFilters:
    async def test_included_spaces_are_requested_by_key(self, atlassian_api, records_db, checkpoints, search) -> None:
        filters = {"sync": {"values": {"space_keys": {"operator": "in", "type": "list", "value": ["ENG", "OPS"]}}}}
        stub_spaces(atlassian_api, space_page([space("ENG", 10), space("OPS", 20)]))
        connector = await make_connector(atlassian_api, records_db, checkpoints, filters=filters)

        await connector.run_sync()

        first = atlassian_api.calls("GET", f"{API}/space")[0]
        assert first.url.params.get_list("spaceKey") == ["ENG", "OPS"]

    async def test_excluded_spaces_are_neither_saved_nor_crawled(self, atlassian_api, records_db, checkpoints, search) -> None:
        filters = {"sync": {"values": {"space_keys": {"operator": "not_in", "type": "list", "value": ["HR"]}}}}
        stub_spaces(atlassian_api, space_page([space("ENG", 10), space("HR", 30)]))
        search.add("page", "HR", 0, listing([content("secret", "HR", 30)]))
        connector = await make_connector(atlassian_api, records_db, checkpoints, filters=filters)

        await connector.run_sync()

        assert set(records_db.record_groups) == {"10"}
        assert not any("space='HR'" in c for c in search.cql)
        assert "secret" not in records_db.records

    async def test_page_filter_is_sent_with_children_and_indexing_off_is_respected(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        filters = {
            "sync": {"values": {"page_ids": {"operator": "not_in", "type": "list", "value": ["99"]}}},
            "indexing": {"values": {"pages": {"operator": "is", "type": "boolean", "value": False}}},
        }
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")]))
        connector = await make_connector(atlassian_api, records_db, checkpoints, filters=filters)

        await connector.run_sync()

        page_cql = next(c for c in search.cql if c.startswith("type=page"))
        assert "NOT (id in (99) OR ancestor in (99))" in page_cql
        assert records_db.records["p1"].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestAttachmentsAndComments:
    async def test_truncated_inline_attachment_list_is_fetched_in_full(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1", attachments=[attachment("att1")], attachment_total=3)]))
        atlassian_api.on(
            "GET",
            f"{API}/content/p1/child/attachment",
            {"results": [attachment("att1"), attachment("att2", "diagram.png", "image/png"), attachment("att3", "notes.txt", "text/plain")], "_links": {"base": BASE}},
        )
        filters = {"indexing": {"values": {"page_attachments": {"operator": "is", "type": "boolean", "value": False}}}}
        connector = await make_connector(atlassian_api, records_db, checkpoints, filters=filters)

        await connector.run_sync()

        files = saved(records_db, RecordType.FILE)
        assert set(files) == {"att1", "att2", "att3"}
        page = records_db.records["p1"]
        for f in files.values():
            assert f.parent_external_record_id == "p1"
            assert f.parent_node_id == page.id
            assert f.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert files["att2"].extension == "png"
        assert files["att1"].size_in_bytes == 2048
        assert files["att1"].weburl == f"{BASE}/download/attachments/att1"

    async def test_footer_and_inline_comments_with_nested_replies_are_synced(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")]))

        def comment(cid: str, location: str = "footer", resolved: Optional[bool] = None) -> dict[str, Any]:
            ext: dict[str, Any] = {"location": location}
            if location == "inline":
                ext["inlineProperties"] = {"originalSelection": "highlighted words"}
                ext["resolution"] = {"resolved": bool(resolved)}
            return {
                "id": cid,
                "title": f"Re: {cid}",
                "version": {"number": 1, "when": "2024-05-02T10:00:00.000Z", "by": {"userKey": "u-42"}},
                "extensions": ext,
                "_links": {"webui": f"/comment/{cid}"},
            }

        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1"), comment("c2", "inline", resolved=True)], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/comment", {"results": [comment("c1-reply")], "_links": {"base": BASE}})
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        page = records_db.records["p1"]
        footer, reply, inline = records_db.records["c1"], records_db.records["c1-reply"], records_db.records["c2"]
        assert footer.record_type == reply.record_type == RecordType.COMMENT
        assert inline.record_type == RecordType.INLINE_COMMENT
        assert footer.parent_external_record_id == "p1"
        assert reply.parent_external_record_id == "c1"
        assert reply.parent_record_type == RecordType.COMMENT
        assert all(c.parent_node_id == page.id for c in (footer, reply, inline))
        assert inline.resolution_status == "resolved"
        assert inline.comment_selection == "highlighted words"
        assert footer.author_source_id == "u-42"

    async def test_comment_list_is_read_to_the_end(self, atlassian_api, records_db, checkpoints, search) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")]))
        by = {"number": 1, "when": "2024-05-02T10:00:00.000Z", "by": {"userKey": "u-1"}}
        first = {"results": [{"id": "c1", "title": "a", "version": by}], "_links": {"base": BASE, "next": f"{API}/content/p1/child/comment?start=1"}}
        second = {"results": [{"id": "c2", "title": "b", "version": by}], "_links": {"base": BASE}}
        seen: list[str] = []

        def comments(request: httpx.Request) -> httpx.Response:
            start = AtlassianApiStub.query(request).get("start", "0")
            seen.append(start)
            return json_response(second if start == "1" else first)

        atlassian_api.on("GET", f"{API}/content/p1/child/comment", comments)
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        assert {"c1", "c2"} <= set(records_db.records)
        assert "1" in seen


class TestPartialFailures:
    async def test_one_bad_page_does_not_stop_the_rest(self, atlassian_api, records_db, checkpoints, search) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1"), content("p2"), content("p3")]))
        records_db.fail_lookup_for = {"p2"}
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        assert set(saved(records_db, RecordType.CONFLUENCE_PAGE)) == {"p1", "p3"}

    async def test_a_failing_space_listing_page_keeps_what_was_already_synced(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")], next_start=1))
        search.add("page", "ENG", 1, json_response({"message": "boom"}, status=500))
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        assert "p1" in records_db.records

    async def test_a_failed_listing_page_does_not_move_the_checkpoint_past_unfetched_pages(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")], next_start=1))
        search.add("page", "ENG", 1, json_response({"message": "rate limited"}, status=429))
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        saved_time = checkpoints.values_for("confluence_pages/ENG")
        assert saved_time is None or saved_time["last_sync_time"] <= "2024-05-01T10:00:00.000Z"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: a page that fails to save "
            "is not retried, because the checkpoint moves past its last-modified time."
        ),
    )
    async def test_a_page_that_failed_is_listed_again_next_time(self, atlassian_api, records_db, checkpoints, search) -> None:
        old = "2024-05-01T10:00:00.000Z"
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1", when=old), content("p2", when=old)]))
        records_db.fail_lookup_for = {"p2"}
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        saved_time = checkpoints.values_for("confluence_pages/ENG")
        assert saved_time is None or saved_time["last_sync_time"] <= old


class TestSpaceHomepage:
    async def test_homepage_missing_from_search_is_backfilled_once(self, atlassian_api, records_db, checkpoints, search) -> None:
        def spaces(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("expand") == "homepage":
                return json_response({"results": [{**space("ENG", 10), "homepage": {"id": 500, "title": "Home"}}]})
            return json_response(space_page([space("ENG", 10)]))

        atlassian_api.on("GET", f"{API}/space", spaces)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        search.add("page", "ENG", 0, listing([content("p1")]))
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        assert {"500", "p1"} <= set(saved(records_db, RecordType.CONFLUENCE_PAGE))
        homepage_saves = [b for b in records_db.record_batches if any(r.external_record_id == "500" for r in b)]
        assert len(homepage_saves) == 1


class TestSyncStopsLoudlyWhenItCannotStart:
    async def test_space_listing_error_is_not_mistaken_for_an_empty_site(self, atlassian_api, records_db, checkpoints) -> None:
        atlassian_api.on("GET", f"{API}/space", json_response({"message": "down"}, status=503))
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        assert records_db.record_groups == {}
        assert checkpoints.sync_points == {}

    async def test_init_failure_raises_instead_of_syncing_nothing(self, records_db, checkpoints) -> None:
        connector = ConfluenceDataCenterPersonalConnector(
            logging.getLogger("t"), records_db, checkpoints, FakeConfigService(CONNECTOR_ID, {}),
            CONNECTOR_ID, "personal", "creator-user-1",
        )
        with pytest.raises(RuntimeError, match="init failed"):
            await connector.run_sync()


async def read_stream(response: StreamingResponse) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


def stored_page(cid: str = "p1", revision: str = "1") -> WebpageRecord:
    return WebpageRecord(
        id=f"rec-{cid}", org_id="org-1", record_name=f"Title {cid}", record_type=RecordType.CONFLUENCE_PAGE,
        external_record_id=cid, external_revision_id=revision, version=0, origin="CONNECTOR", connector_name="CONFLUENCE DATA CENTER PERSONAL",
        connector_id=CONNECTOR_ID, record_group_type=RecordGroupType.CONFLUENCE_SPACES, external_record_group_id="10",
        mime_type="text/html",
    )


def stored_comment(cid: str = "c1", parent: str = "p1", parent_type: RecordType = RecordType.WEBPAGE, revision: str = "1") -> CommentRecord:
    return CommentRecord(
        id=f"rec-{cid}", org_id="org-1", record_name="Re: page", record_type=RecordType.COMMENT, external_record_id=cid,
        external_revision_id=revision, version=0, origin="CONNECTOR", connector_name="CONFLUENCE DATA CENTER PERSONAL", connector_id=CONNECTOR_ID,
        parent_external_record_id=parent, parent_record_type=parent_type, external_record_group_id="10",
        record_group_type=RecordGroupType.CONFLUENCE_SPACES, mime_type="text/html", author_source_id="u-1",
    )


def stored_file(aid: str = "att1", parent: str = "p1", parent_type: RecordType = RecordType.WEBPAGE, revision: str = "1") -> FileRecord:
    return FileRecord(
        id=f"rec-{aid}", org_id="org-1", record_name="report.pdf", record_type=RecordType.FILE, external_record_id=aid,
        external_revision_id=revision, version=0, origin="CONNECTOR", connector_name="CONFLUENCE DATA CENTER PERSONAL", connector_id=CONNECTOR_ID,
        parent_external_record_id=parent, parent_record_type=parent_type, external_record_group_id="10",
        record_group_type=RecordGroupType.CONFLUENCE_SPACES, mime_type="application/pdf", is_file=True, extension="pdf",
    )


class TestOpeningAPageOrFile:
    async def test_page_html_is_streamed_with_title_and_inlined_images(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        body = '<p>Hello</p><img src="/download/attachments/p1/diagram.png"><img src="https://elsewhere.example.org/x.png">'
        atlassian_api.on("GET", f"{API}/content/p1", {"id": "p1", "title": "Runbook", "body": {"export_view": {"value": body}}, "_links": {"base": BASE}})
        atlassian_api.on("GET", "/download/attachments/p1/diagram.png", httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}))

        html = (await read_stream(await connector.stream_record(stored_page()))).decode()

        assert html.startswith("<h1>Runbook</h1>")
        assert "data:image/png;base64," in html
        assert "https://elsewhere.example.org/x.png" in html, "images from other sites are never fetched with our token"
        assert not any("elsewhere" in str(r.url) for r in atlassian_api.requests)

    async def test_rate_limited_page_fetch_waits_as_told_then_succeeds(self, atlassian_api, records_db, checkpoints, backoff_sleeps) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        ok = json_response({"id": "p1", "title": "T", "body": {"export_view": {"value": "<p>x</p>"}}})
        atlassian_api.on("GET", f"{API}/content/p1", [json_response({}, status=429, headers={"Retry-After": "7"}), ok])

        html = (await read_stream(await connector.stream_record(stored_page()))).decode()

        assert "<p>x</p>" in html
        assert backoff_sleeps == [7.0]
        assert len(atlassian_api.calls("GET", f"{API}/content/p1")) == 2

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(401, 409), (403, 403), (404, 404)],
        ids=["expired-token-asks-to-reconnect", "no-access-says-forbidden", "gone-says-not-found"],
    )
    async def test_source_errors_are_reported_honestly(self, atlassian_api, records_db, checkpoints, status, expected) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/content/p1", json_response({}, status=status))

        with pytest.raises(HTTPException) as err:
            await connector.stream_record(stored_page())

        assert err.value.status_code == expected
        assert len(atlassian_api.calls("GET", f"{API}/content/p1")) == 1, "permanent errors are not retried"

    async def test_comment_html_is_streamed(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/content/c1", {"id": "c1", "body": {"export_view": {"value": "<p>LGTM</p>"}}, "_links": {"base": BASE}})

        html = (await read_stream(await connector.stream_record(stored_comment()))).decode()

        assert "<p>LGTM</p>" in html and "<h1>Re: page</h1>" in html

    async def test_attachment_bytes_are_streamed_after_a_transient_failure(
        self, atlassian_api, records_db, checkpoints, backoff_sleeps
    ) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/content/att1", {"id": "att1", "_links": {"download": "/download/attachments/p1/report.pdf"}})
        payload = b"%PDF" + b"x" * 20000
        atlassian_api.on("GET", "/download/attachments/p1/report.pdf", [httpx.Response(502), httpx.Response(200, content=payload)])

        response = await connector.stream_record(stored_file())

        assert await read_stream(response) == payload
        assert len(backoff_sleeps) == 1

    async def test_attachment_without_download_link_is_not_found(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/content/att1", {"id": "att1", "_links": {}})

        with pytest.raises(HTTPException) as err:
            await read_stream(await connector.stream_record(stored_file()))
        assert err.value.status_code == 404

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits the Confluence client: Atlassian documents an "
            "attachment's download link as relative to the site's base address, but the client joins it "
            "to the bare host, so on a Data Center site served under a sub-path (for example "
            "https://intranet.example.com/confluence) every attachment download points at the wrong URL."
        ),
    )
    async def test_attachment_download_keeps_the_sites_sub_path(self, atlassian_api, checkpoints) -> None:
        db = FakeRecordsDb()
        base = "https://intranet.example.com/confluence"
        config = {"auth": {"authType": "API_TOKEN", "baseUrl": base, "apiToken": FAKE_PAT}}
        connector = ConfluenceDataCenterPersonalConnector(
            logging.getLogger("t"), db, checkpoints, FakeConfigService(CONNECTOR_ID, config), CONNECTOR_ID, "personal", "u",
        )
        assert await connector.init()
        atlassian_api.install(connector.external_client.get_client())
        atlassian_api.on("GET", "/confluence/rest/api/content/att1", {"id": "att1", "_links": {"base": base, "context": "/confluence", "download": "/download/attachments/p1/report.pdf"}})
        atlassian_api.on("GET", "/confluence/download/attachments/p1/report.pdf", httpx.Response(200, content=b"%PDF"))

        try:
            data = await read_stream(await connector.stream_record(stored_file()))
        except HTTPException:
            data = None

        downloads = [r.url.path for r in atlassian_api.requests if "/download/" in r.url.path]
        assert set(downloads) == {"/confluence/download/attachments/p1/report.pdf"}
        assert data == b"%PDF"

    async def test_unknown_record_type_is_rejected(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        record = stored_page()
        record.record_type = RecordType.MAIL
        with pytest.raises(HTTPException) as err:
            await connector.stream_record(record)
        assert err.value.status_code == 400


class TestReindex:
    async def test_changed_items_are_refreshed_and_unchanged_ones_just_reindexed(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        records_db.records["p1"] = stored_page("p1")
        atlassian_api.on("GET", f"{API}/content/p1", {**content("p1"), "version": {"number": 4}, "ancestors": [{"id": "p0", "type": "page"}]})
        atlassian_api.on("GET", f"{API}/content/p2", {**content("p2"), "version": {"number": 1}})
        atlassian_api.on("GET", f"{API}/content/c1", {"id": "c1", "title": "Re", "version": {"number": 2, "by": {"userKey": "u-9"}, "when": "2024-06-01T00:00:00.000Z"}, "_links": {"base": BASE, "webui": "/c1"}})
        atlassian_api.on("GET", f"{API}/content/att1", {**attachment("att1"), "version": {"number": 3}})

        page, unchanged, comment, file = stored_page("p1"), stored_page("p2"), stored_comment("c1"), stored_file("att1")
        await connector.reindex_records([page, unchanged, comment, file])

        refreshed = {r.external_record_id: r for r in records_db.content_updates}
        assert set(refreshed) == {"p1", "c1", "att1"}
        assert refreshed["p1"].id == "rec-p1" and refreshed["p1"].external_revision_id == "4"
        assert refreshed["p1"].parent_external_record_id == "p0", "a move is picked up on reindex"
        assert refreshed["c1"].parent_node_id == "rec-p1"
        assert refreshed["att1"].parent_node_id == "rec-p1"
        assert [r.external_record_id for r in records_db.reindexed] == ["p2"]

    async def test_item_deleted_at_source_does_not_break_the_batch(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/content/p2", {**content("p2"), "version": {"number": 9}})

        await connector.reindex_records([stored_page("gone"), stored_page("p2")])

        assert [r.external_record_id for r in records_db.content_updates] == ["p2"]
        assert [r.external_record_id for r in records_db.reindexed] == ["gone"]

    async def test_reply_attachment_resolves_its_page_through_the_comment(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        records_db.records["c1"] = stored_comment("c1")
        atlassian_api.on("GET", f"{API}/content/att1", {**attachment("att1"), "version": {"number": 2}})
        atlassian_api.on("GET", f"{API}/content/c1", {"id": "c1", "container": {"type": "page", "id": "p1"}})

        await connector.reindex_records([stored_file("att1", parent="c1", parent_type=RecordType.COMMENT)])

        (updated,) = records_db.content_updates
        assert updated.parent_external_record_id == "c1"
        assert updated.parent_record_type == RecordType.COMMENT
        assert updated.parent_node_id == "rec-c1"

    async def test_reindex_refuses_to_run_before_init(self, records_db, checkpoints) -> None:
        connector = ConfluenceDataCenterPersonalConnector(
            logging.getLogger("t"), records_db, checkpoints, FakeConfigService(CONNECTOR_ID, {}), CONNECTOR_ID, "personal", "u",
        )
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([stored_page()])


class TestFilterOptions:
    async def test_space_options_page_through_the_space_list(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/space", space_page([space("ENG", 10), space("OPS", 20)], next_start=2))

        first = await connector.get_filter_options("space_keys", limit=2)

        assert [(o.id, o.label) for o in first.options] == [("ENG", "ENG space"), ("OPS", "OPS space")]
        assert first.has_more and first.cursor == "2"

        atlassian_api.on("GET", f"{API}/space", space_page([space("HR", 30)]))
        second = await connector.get_filter_options("space_keys", limit=2, cursor=first.cursor)
        assert [o.id for o in second.options] == ["HR"] and second.has_more is False
        assert AtlassianApiStub.query(atlassian_api.requests[-1])["start"] == "2"

    async def test_space_search_uses_fuzzy_title_match(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/search", {"results": [{"space": {"key": "ENG", "name": "Engineering"}}], "_links": {}})

        options = await connector.get_filter_options("space_keys", search='Eng"ineering')

        assert [(o.id, o.label) for o in options.options] == [("ENG", "Engineering")]
        cql = AtlassianApiStub.query(atlassian_api.requests[-1])["cql"]
        assert cql.startswith("type=space and space.title ~") and 'Eng\\"ineering*' in cql

    async def test_page_and_blogpost_options(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/content/search", listing([content("p1")], next_start=1))
        atlassian_api.on("GET", f"{API}/search", {"results": [{"content": {"id": "b1", "title": "Launch", "type": "blogpost"}}], "_links": {}})

        pages = await connector.get_filter_options("page_ids")
        blogs = await connector.get_filter_options("blogpost_ids", search="Laun")

        assert [(o.id, o.label) for o in pages.options] == [("p1", "Title p1")] and pages.cursor == "1"
        assert [o.id for o in blogs.options] == ["b1"]

    async def test_options_error_is_raised_not_hidden(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/space", json_response({}, status=500))
        with pytest.raises(RuntimeError):
            await connector.get_filter_options("space_keys")
        with pytest.raises(ValueError):
            await connector.get_filter_options("labels")


class TestCommentAttachments:
    @staticmethod
    def _comment(cid: str, body: str = "") -> dict[str, Any]:
        return {
            "id": cid,
            "title": f"Re: {cid}",
            "version": {"number": 1, "when": "2024-05-02T10:00:00.000Z", "by": {"userKey": "u-1"}},
            "body": {"storage": {"value": body}},
            "_links": {"webui": f"/c/{cid}"},
        }

    async def test_files_attached_to_a_comment_hang_off_that_comment(self, atlassian_api, records_db, checkpoints, search) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [self._comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/attachment", {"results": [attachment("att-c", "log.txt", "text/plain")], "_links": {"base": BASE}})
        filters = {"indexing": {"values": {"page_attachments": {"operator": "is", "type": "boolean", "value": False}}}}
        connector = await make_connector(atlassian_api, records_db, checkpoints, filters=filters)

        await connector.run_sync()

        f = records_db.records["att-c"]
        assert f.parent_external_record_id == "c1"
        assert f.parent_record_type == RecordType.COMMENT
        assert f.parent_node_id == records_db.records["c1"].id
        assert f.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_image_embedded_in_a_reply_is_synced_under_the_reply(self, atlassian_api, records_db, checkpoints, search) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1")]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [self._comment("c1")], "_links": {"base": BASE}})
        embed = '<ac:image><ri:attachment ri:filename="Shot.PNG" /></ac:image>'
        atlassian_api.on("GET", f"{API}/content/c1/child/comment", {"results": [self._comment("c1-r", embed)], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1-r/child/attachment", [
            {"results": [], "_links": {"base": BASE}},
            {"results": [attachment("att-s", "shot.png", "image/png")], "_links": {"base": BASE}},
        ])
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        f = records_db.records["att-s"]
        assert f.parent_external_record_id == "c1-r"
        assert f.parent_node_id == records_db.records["c1-r"].id

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: on the first sync, a page image "
            "that a comment also shows is saved twice in the same batch (once under the page, once under "
            "the comment), because the duplicate check looks in the database before the batch is written."
        ),
    )
    async def test_page_image_shown_in_a_comment_is_saved_once_under_the_page(
        self, atlassian_api, records_db, checkpoints, search
    ) -> None:
        stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
        search.add("page", "ENG", 0, listing([content("p1", attachments=[attachment("att1", "diagram.png", "image/png")])]))
        embed = '<ac:image><ri:attachment ri:filename="diagram.png" /></ac:image>'
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [self._comment("c1", embed)], "_links": {"base": BASE}})
        connector = await make_connector(atlassian_api, records_db, checkpoints)

        await connector.run_sync()

        saved_copies = [r for batch in records_db.record_batches for r in batch if r.external_record_id == "att1"]
        assert len(saved_copies) == 1
        assert saved_copies[0].parent_external_record_id == "p1"


class TestMoreReindexShapes:
    async def test_changed_blogpost_is_refreshed(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        atlassian_api.on("GET", f"{API}/content/b1", {**content("b1", ctype="blogpost"), "version": {"number": 5}})
        blog = stored_page("b1")
        blog.record_type = RecordType.CONFLUENCE_BLOGPOST

        await connector.reindex_records([blog])

        (updated,) = records_db.content_updates
        assert updated.record_type == RecordType.CONFLUENCE_BLOGPOST and updated.version == 1

    async def test_changed_inline_reply_keeps_its_parent_comment(self, atlassian_api, records_db, checkpoints) -> None:
        connector = await make_connector(atlassian_api, records_db, checkpoints)
        records_db.records["p1"] = stored_page("p1")
        atlassian_api.on("GET", f"{API}/content/c2", {
            "id": "c2", "title": "Re", "container": {"type": "page", "id": "p1"},
            "ancestors": [{"type": "page", "id": "p1"}, {"type": "comment", "id": "c1"}],
            "version": {"number": 3, "by": {"username": "jdoe"}, "when": "2024-06-01T00:00:00.000Z"},
            "extensions": {"location": "inline", "resolution": {"resolved": False}, "inlineProperties": {"originalSelection": "word"}},
            "_links": {"base": BASE, "webui": "/c2"},
        })
        reply = stored_comment("c2", parent="p1")
        reply.record_type = RecordType.INLINE_COMMENT

        await connector.reindex_records([reply])

        (updated,) = records_db.content_updates
        assert updated.record_type == RecordType.INLINE_COMMENT
        assert updated.parent_external_record_id == "c1"
        assert updated.resolution_status == "open" and updated.comment_selection == "word"
        assert updated.author_source_id == "jdoe"
        assert updated.weburl == f"{BASE}/c2"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Bug, left alone because an open PR edits this connector: the 'Index Page Comments' "
        "switch is read but never applied, so comments are indexed even when it is off."
    ),
)
async def test_switching_off_comment_indexing_is_respected(
    atlassian_api: AtlassianApiStub, records_db: FakeRecordsDb, checkpoints: FakeCheckpointStore, search: ContentSearch
) -> None:
    stub_spaces(atlassian_api, space_page([space("ENG", 10)]))
    search.add("page", "ENG", 0, listing([content("p1")]))
    reply = {"id": "c1", "title": "Re", "version": {"number": 1, "by": {"userKey": "u-1"}}}
    filters = {"indexing": {"values": {"page_comments": {"operator": "is", "type": "boolean", "value": False}}}}
    connector = await make_connector(atlassian_api, records_db, checkpoints, filters=filters)
    atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [reply], "_links": {"base": BASE}})

    await connector.run_sync()

    assert records_db.records["c1"].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

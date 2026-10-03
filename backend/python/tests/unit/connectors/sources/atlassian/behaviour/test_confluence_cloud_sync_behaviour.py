"""Confluence Cloud connector, driven over a fake Atlassian API with its real OAuth client.

The connector, the Confluence client and request builder, and the HTTP client
are real. Every HTTP request (including the one-off site lookup on
api.atlassian.com) is answered by an in-memory stub, and our databases are
in-memory fakes.
"""

import logging
import re
from collections.abc import Callable
from typing import Any, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    FakeConfigService,
    json_response,
)
from atlassian_cloud_fakes import (
    CLOUD_ID,
    RESOURCES_PATH,
    SITE,
    CloudRecordsDb,
    RecordingNotifications,
    bearer,
    drain_notifications,
    oauth_config,
    one_site,
    route_every_http_client,
)
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.models.entities import (
    AppUser,
    CommentRecord,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, PermissionType
from app.sources.client.confluence.confluence import ConfluenceRESTClientViaToken
from app.sources.external.confluence.confluence import ConfluenceDataSource

CONNECTOR_ID = "confluence-cloud-1"
V2 = f"/ex/confluence/{CLOUD_ID}/wiki/api/v2"
V1 = f"/ex/confluence/{CLOUD_ID}/wiki/rest/api"
WIKI = f"{SITE}/wiki"


@pytest.fixture
def db() -> CloudRecordsDb:
    return CloudRecordsDb()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> AtlassianApiStub:
    stub = AtlassianApiStub()
    route_every_http_client(monkeypatch, stub)
    one_site(stub)
    stub.on_suffix("GET", "/restriction", {"results": []})
    stub.on_suffix("GET", "/footer-comments", {"results": []})
    stub.on_suffix("GET", "/inline-comments", {"results": []})
    return stub


async def make_connector(
    db: CloudRecordsDb, checkpoints: FakeCheckpointStore, config: Optional[dict] = None
) -> tuple[ConfluenceConnector, FakeConfigService]:
    config_service = FakeConfigService(CONNECTOR_ID, config or oauth_config())
    connector = ConfluenceConnector(
        logging.getLogger("test.confluence_cloud"), db, checkpoints, config_service, CONNECTOR_ID, "team", "creator-1"
    )
    connector._notification_service = RecordingNotifications()
    return connector, config_service


async def ready_connector(db, checkpoints, config=None) -> tuple[ConfluenceConnector, FakeConfigService]:
    connector, config_service = await make_connector(db, checkpoints, config)
    assert await connector.init() is True
    return connector, config_service


def v1_page(pid: str, *, version: int = 1, space_id: int = 77, attachments: Optional[list] = None) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "page",
        "title": f"Page {pid}",
        "space": {"id": space_id, "key": "ENG"},
        "history": {"createdDate": "2024-01-01T00:00:00.000Z", "lastUpdated": {"when": "2024-05-01T10:00:00.000Z", "number": version}},
        "ancestors": [],
        "children": {"attachment": {"results": attachments or [], "size": len(attachments or [])}},
        "_links": {"webui": f"/spaces/ENG/pages/{pid}", "self": f"{WIKI}/rest/api/content/{pid}"},
    }


def search_page(results: list, cursor: Optional[str] = None) -> dict[str, Any]:
    links: dict[str, Any] = {"base": WIKI}
    if cursor:
        links["next"] = f"/rest/api/content/search?next=true&cursor={cursor}&limit=50"
    return {"results": results, "_links": links}


def read_restricted_to(*account_ids: str, groups: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"results": [{"operation": "read", "restrictions": {
        "user": {"results": [{"accountId": a} for a in account_ids]},
        "group": {"results": [{"id": g, "name": g} for g in groups]},
    }}]}


SPEC = {"id": "att2", "title": "spec.pdf", "mediaType": "application/pdf", "fileSize": 10, "version": {"number": 1}}


class ContentSearch:
    """Answers v1 content search by cursor; remembers each query.

    When ``folders`` or ``blogposts`` is set, those searches get it instead. When ``indexed`` is
    set, a search narrowed to ``id in (...)`` gets the indexed items among those
    ids, less any a ``NOT (id in (...))`` filter clause names, or ``indexed``
    itself if it is a response.
    """

    def __init__(self) -> None:
        self.by_cursor: dict[Optional[str], Any] = {}
        self.queries: list[dict[str, str]] = []
        self.folders: list[dict[str, Any]] | None = None
        self.blogposts: list[dict[str, Any]] | None = None
        self.indexed: dict[str, dict[str, Any]] | httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        self.queries.append(q)
        cql = q.get("cql", "")
        if self.folders is not None and cql.startswith("type=folder"):
            return json_response(search_page(self.folders))
        if self.blogposts is not None and cql.startswith("type=blogpost"):
            return json_response(search_page(self.blogposts))
        by_id = re.search(r"(?:^| AND )id in \(([^)]*)\)", cql)
        if self.indexed is not None and by_id:
            if isinstance(self.indexed, httpx.Response):
                return self.indexed
            ids = [i.strip() for i in by_id.group(1).split(",")]
            excluded = re.search(r"NOT \(id in \(([^)]*)\)", cql)
            left_out = {i.strip() for i in excluded.group(1).split(",")} if excluded else set()
            return json_response(search_page([self.indexed[i] for i in ids if i in self.indexed and i not in left_out]))
        answer = self.by_cursor.get(q.get("cursor"), search_page([]))
        return answer if isinstance(answer, httpx.Response) else json_response(answer)


@pytest.fixture
def search(api: AtlassianApiStub) -> ContentSearch:
    handler = ContentSearch()
    api.on("GET", f"{V1}/content/search", handler)
    return handler


class TestOAuthClientAndTokenRefresh:
    async def test_init_finds_the_site_and_calls_it_with_the_oauth_token(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)

        assert type(connector.data_source) is ConfluenceDataSource
        client = connector.external_client.get_client()
        assert type(client) is ConfluenceRESTClientViaToken
        assert client.get_base_url() == f"https://api.atlassian.com{V2}"

        api.on("GET", f"{V2}/spaces", {"results": []})
        assert await connector.test_connection_and_access() is True
        request = api.calls("GET", f"{V2}/spaces")[-1]
        assert request.url.host == "api.atlassian.com"
        assert bearer(request) == "Bearer fake-access-1"
        assert bearer(api.calls("GET", RESOURCES_PATH)[0]) == "Bearer fake-access-1"

    async def test_a_token_refreshed_in_the_background_is_used_on_the_next_request(self, api, db, checkpoints) -> None:
        connector, config_service = await ready_connector(db, checkpoints)
        api.on("GET", f"{V2}/spaces", {"results": []})

        config_service.config["credentials"]["access_token"] = "fake-access-2"
        await connector.test_connection_and_access()

        assert bearer(api.calls("GET", f"{V2}/spaces")[-1]) == "Bearer fake-access-2"
        assert connector.external_client.get_client().get_token() == "fake-access-2"

    async def test_a_lost_token_stops_the_sync_with_a_clear_message(self, api, db, checkpoints) -> None:
        connector, config_service = await ready_connector(db, checkpoints)
        config_service.config["credentials"]["access_token"] = ""

        with pytest.raises(HTTPException) as err:
            await connector._sync_spaces()

        assert err.value.status_code == 409
        assert "is not connected. Check its settings and try again." in err.value.detail
        assert api.calls("GET", f"{V2}/spaces") == [], "nothing is sent without a token"
        assert await connector.test_connection_and_access() is False

    async def test_rejected_token_at_setup_is_reported_not_swallowed(self, api, db, checkpoints) -> None:
        api.on("GET", RESOURCES_PATH, json_response({"message": "Unauthorized"}, status=401))
        connector, _ = await make_connector(db, checkpoints)
        assert await connector.init() is False
        assert connector.external_client is None

    async def test_a_token_reaching_several_sites_asks_for_a_single_site_app(self, api, db, checkpoints) -> None:
        api.on("GET", RESOURCES_PATH, json_response([
            {"id": "a", "name": "a", "url": "https://a.atlassian.net"},
            {"id": "b", "name": "b", "url": "https://b.atlassian.net"},
        ]))
        connector, _ = await make_connector(db, checkpoints, oauth_config(base_url=None))

        with pytest.raises(ConnectorInitError, match="multiple"):
            await connector.init()
        await drain_notifications(connector)

        (note,) = connector._notification_service.sent
        assert "single-site" in note["message"] and "reconnect" in note["message"]


class TestSpacesAndPermissions:
    async def test_all_space_pages_are_read_and_members_mapped_to_access(self, api, db, checkpoints) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        db.add_group("grp-eng")
        page_one = {
            "results": [{"id": "1", "key": "ENG", "name": "Engineering", "_links": {"webui": "/spaces/ENG"}}],
            "_links": {"base": WIKI, "next": f"{V2}/spaces?cursor=CUR2&limit=20"},
        }
        page_two = {"results": [{"id": "2", "key": "OPS", "name": "Ops"}], "_links": {"base": WIKI}}

        def spaces(request: httpx.Request) -> httpx.Response:
            return json_response(page_two if AtlassianApiStub.query(request).get("cursor") == "CUR2" else page_one)

        def perms(principal: dict, key: str, target: str = "space") -> dict:
            return {"principal": principal, "operation": {"key": key, "targetType": target}}

        api.on("GET", f"{V2}/spaces", spaces)
        api.on("GET", f"{V2}/spaces/1/permissions", {"results": [
            perms({"type": "user", "id": "acc-ana"}, "read"),
            perms({"type": "user", "id": "acc-ana"}, "administer"),
            perms({"type": "group", "id": "grp-eng"}, "read"),
            perms({"type": "user", "id": "acc-stranger"}, "read"),
            perms({"type": "group", "id": "grp-unknown"}, "read"),
        ]})
        api.on("GET", f"{V2}/spaces/2/permissions", {"results": []})
        connector, _ = await ready_connector(db, checkpoints)

        spaces_synced = await connector._sync_spaces()

        assert [s.short_name for s in spaces_synced] == ["ENG", "OPS"]
        assert db.record_groups["1"].web_url == f"{WIKI}/spaces/ENG"
        grants = {(p.entity_type, p.email or p.external_id, p.type) for p in db.record_group_permissions["1"]}
        assert (EntityType.USER, "ana@acme.com", PermissionType.OWNER) in grants
        assert (EntityType.GROUP, "grp-eng", PermissionType.READ) in grants
        assert not any("stranger" in str(g) or "unknown" in str(g) for g in grants), "unknown principals get nothing"
        assert db.record_group_permissions["2"] == []

    async def test_a_failed_space_permission_read_keeps_the_stored_access(self, api, db, checkpoints) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        api.on("GET", f"{V2}/spaces", {"results": [{"id": "1", "key": "ENG", "name": "Engineering"}], "_links": {"base": WIKI}})
        grant = {"principal": {"type": "user", "id": "acc-ana"}, "operation": {"key": "read", "targetType": "space"}}
        api.on("GET", f"{V2}/spaces/1/permissions", {"results": [grant]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_spaces()
        before = [(p.email, p.type) for p in db.record_group_permissions["1"]]
        assert before, "the first sync grants access"

        api.on("GET", f"{V2}/spaces/1/permissions", json_response({"message": "busy"}, status=503))
        spaces_synced = await connector._sync_spaces()

        assert [s.short_name for s in spaces_synced] == ["ENG"], "the space's content is still synced"
        assert [(p.email, p.type) for p in db.record_group_permissions["1"]] == before

    async def test_excluded_space_is_not_saved(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V2}/spaces", {"results": [{"id": "1", "key": "ENG", "name": "E"}, {"id": "9", "key": "HR", "name": "H"}]})
        api.on_suffix("GET", "/permissions", {"results": []})
        connector, _ = await ready_connector(db, checkpoints)
        connector.sync_filters = type(connector.sync_filters).from_dict(
            {"space_keys": {"operator": "not_in", "type": "list", "value": ["HR"]}}
        )

        await connector._sync_spaces()

        assert set(db.record_groups) == {"1"}


class TestPageSync:
    async def test_every_result_page_is_synced_then_only_changes_are_asked_for(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10"), v1_page("11")], cursor="C2")
        search.by_cursor["C2"] = search_page([v1_page("12")])
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert {"10", "11", "12"} <= set(db.records)
        assert [q.get("cursor") for q in search.queries] == [None, "C2"]
        assert "lastModified >" not in search.queries[0]["cql"]
        checkpoint = checkpoints.values_for("confluence_pages/ENG")
        assert checkpoint and checkpoint["last_sync_time"].endswith("Z")
        first_id = db.records["10"].id

        search.queries.clear()
        search.by_cursor = {None: search_page([v1_page("10", version=2)])}
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert "lastModified >" in search.queries[0]["cql"]
        assert db.records["10"].id == first_id and db.records["10"].version == 1
        assert len([r for r in db.records.values() if isinstance(r, WebpageRecord)]) == 3, "no duplicates"

    async def test_view_restricted_page_is_limited_to_the_named_people(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        db.add_group("grp-leads")
        search.by_cursor[None] = search_page([v1_page("10"), v1_page("11")])
        api.on("GET", f"{V1}/content/10/restriction", {"results": [
            {"operation": "read", "restrictions": {
                "user": {"results": [{"accountId": "acc-ana"}, {"accountId": "acc-no-email"}]},
                "group": {"results": [{"id": "grp-leads", "name": "leads"}]},
            }},
        ]})
        api.on("GET", f"{V1}/content/11/restriction", {"results": [
            {"operation": "update", "restrictions": {"user": {"results": [{"accountId": "acc-ana"}]}, "group": {"results": []}}},
        ]})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        restricted, edit_only = db.records["10"], db.records["11"]
        assert restricted.inherit_permissions is False
        grants = {(p.entity_type, p.email or p.external_id, p.type) for p in db.record_permissions["10"]}
        assert grants == {
            (EntityType.USER, "ana@acme.com", PermissionType.READ),
            (EntityType.GROUP, "acc-no-email", PermissionType.READ),
            (EntityType.GROUP, "grp-leads", PermissionType.READ),
        }
        assert [g.source_user_group_id for g, _ in db.user_groups] == ["acc-no-email"], "a stand-in group keeps the grant for a user without email"
        assert edit_only.inherit_permissions is True, "an edit-only restriction does not hide the page from space members"
        assert {p.type for p in db.record_permissions["11"]} == {PermissionType.WRITE}

    async def test_a_failed_restriction_lookup_never_opens_a_page_to_the_whole_space(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10")])
        api.on("GET", f"{V1}/content/10/restriction", json_response({"message": "rate limited"}, status=429))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        page = db.records.get("10")
        assert page is None or page.inherit_permissions is False

    async def test_one_bad_page_does_not_stop_the_rest(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10"), v1_page("11"), v1_page("12")])
        db.fail_lookup_for = {"11"}
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert {"10", "12"} <= set(db.records) and "11" not in db.records

    async def test_a_failed_listing_page_does_not_move_the_checkpoint(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10")], cursor="C2")
        search.by_cursor["C2"] = json_response({"message": "Service Unavailable"}, status=503)
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert checkpoints.values_for("confluence_pages/ENG") is None

    async def test_a_failed_folder_listing_page_does_not_move_the_folder_checkpoint(self, api, db, checkpoints, search) -> None:
        folder = {**v1_page("500"), "type": "folder", "title": "Specs"}
        search.by_cursor[None] = search_page([folder], cursor="C2")
        search.by_cursor["C2"] = json_response({"message": "Service Unavailable"}, status=503)
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_folders("ENG")

        assert "500" in db.records
        assert checkpoints.values_for("confluence_folders/ENG") is None

    @staticmethod
    def _many_attachments(api: AtlassianApiStub, search: ContentSearch, second_page: object) -> None:
        def att(i: int) -> dict[str, Any]:
            return {"id": f"att{i}", "title": f"file{i}.pdf", "mediaType": "application/pdf", "fileSize": 10,
                    "version": {"number": 1}, "_links": {"download": f"/download/attachments/10/file{i}.pdf"}}

        search.by_cursor[None] = search_page([v1_page("10", attachments=[att(0)])])
        first = {"results": [att(i) for i in range(100)], "_links": {"base": WIKI, "next": f"{V2}/pages/10/attachments?cursor=A2"}}
        second = second_page if second_page is not None else {"results": [att(i) for i in range(100, 130)], "_links": {"base": WIKI}}

        def attachments(request: httpx.Request) -> httpx.Response:
            page = second if AtlassianApiStub.query(request).get("cursor") == "A2" else first
            return page if isinstance(page, httpx.Response) else json_response(page)

        api.on("GET", f"{V2}/pages/10/attachments", attachments)
        api.on("GET", f"{V2}/pages/10", {"id": "10", "body": {"atlas_doc_format": {"value": '{"type":"doc","content":[]}'}}})

    async def test_every_attachment_of_a_page_is_synced(self, api, db, checkpoints, search) -> None:
        self._many_attachments(api, search, None)
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        files = [r for r in db.records.values() if isinstance(r, FileRecord)]
        assert len(files) == 130
        assert checkpoints.values_for("confluence_pages/ENG") is not None

    @pytest.mark.parametrize(
        ("first_page", "checkpoint_moves"),
        [
            (json_response({"message": "Service Unavailable"}, status=503), False),
            (json_response({"message": "Not Found"}, status=404), True),
            ({"results": [], "_links": {"base": WIKI, "next": f"{V2}/pages/10/attachments?limit=100"}}, False),
        ],
        ids=["temporary-first-page-failure-holds", "permanent-failure-falls-back", "unfollowable-next-link-holds"],
    )
    async def test_an_attachment_list_that_cannot_be_read_in_full_keeps_the_checkpoint(
        self, api, db, checkpoints, search, first_page, checkpoint_moves
    ) -> None:
        att = {"id": "att0", "title": "file0.pdf", "mediaType": "application/pdf", "fileSize": 10,
               "version": {"number": 1}, "_links": {"download": "/download/attachments/10/file0.pdf"}}
        search.by_cursor[None] = search_page([v1_page("10", attachments=[att])])
        api.on("GET", f"{V2}/pages/10/attachments", first_page if isinstance(first_page, httpx.Response) else json_response(first_page))
        api.on("GET", f"{V2}/pages/10", {"id": "10", "body": {"atlas_doc_format": {"value": '{"type":"doc","content":[]}'}}})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert "att0" in db.records, "the attachment from the search result is still saved"
        assert (checkpoints.values_for("confluence_pages/ENG") is not None) is checkpoint_moves

    async def test_a_cursor_that_comes_round_again_ends_the_list_and_keeps_the_checkpoint(
        self, api, db, checkpoints, search
    ) -> None:
        def att(i: int) -> dict[str, Any]:
            return {"id": f"att{i}", "title": f"file{i}.pdf", "mediaType": "application/pdf", "fileSize": 10,
                    "version": {"number": 1}, "_links": {"download": f"/download/attachments/10/file{i}.pdf"}}

        pages = {
            None: {"results": [att(0)], "_links": {"base": WIKI, "next": f"{V2}/pages/10/attachments?cursor=A2"}},
            "A2": {"results": [att(1)], "_links": {"base": WIKI, "next": f"{V2}/pages/10/attachments?cursor=A3"}},
            "A3": {"results": [att(2)], "_links": {"base": WIKI, "next": f"{V2}/pages/10/attachments?cursor=A2"}},
        }
        search.by_cursor[None] = search_page([v1_page("10", attachments=[att(0)])])
        api.on("GET", f"{V2}/pages/10/attachments", lambda r: json_response(pages[AtlassianApiStub.query(r).get("cursor")]))
        api.on("GET", f"{V2}/pages/10", {"id": "10", "body": {"atlas_doc_format": {"value": '{"type":"doc","content":[]}'}}})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert len(api.calls("GET", f"{V2}/pages/10/attachments")) == 3, "A2 is not read a second time"
        assert {"att0", "att1", "att2"} <= set(db.records)
        assert checkpoints.values_for("confluence_pages/ENG") is None

    async def test_opening_a_page_reads_every_attachment_for_its_images(self, api, db, checkpoints, search) -> None:
        self._many_attachments(api, search, None)
        connector, _ = await ready_connector(db, checkpoints)
        page = WebpageRecord(
            org_id="org-1", record_name="Page 10", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="10",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR, version=0,
            weburl=f"{WIKI}/pages/10",
        )

        await connector.stream_record(page)

        listed = [AtlassianApiStub.query(r).get("cursor") for r in api.calls("GET", f"{V2}/pages/10/attachments")]
        assert "A2" in listed, "the attachment list is followed past the first 100"
        assert len(await connector._fetch_page_attachments_list("10", RecordType.CONFLUENCE_PAGE)) == 130

    async def test_a_failed_second_page_of_attachments_keeps_the_checkpoint(self, api, db, checkpoints, search) -> None:
        self._many_attachments(api, search, json_response({"message": "Service Unavailable"}, status=503))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        files = [r for r in db.records.values() if isinstance(r, FileRecord)]
        assert len(files) == 100, "what was listed is still saved"
        assert checkpoints.values_for("confluence_pages/ENG") is None, "the rest are listed again next sync"

    async def test_images_shown_inside_the_page_are_not_duplicated_as_files(self, api, db, checkpoints, search) -> None:
        shot = {"id": "att1", "title": "shot.png", "mediaType": "image/png", "fileId": "media-1", "version": {"number": 1}}
        spec = {"id": "att2", "title": "spec.pdf", "mediaType": "application/pdf", "fileId": "media-2", "version": {"number": 1}}
        search.by_cursor[None] = search_page([v1_page("10", attachments=[shot, spec])])
        api.on("GET", f"{V2}/pages/10/attachments", {"results": [shot, spec], "_links": {"base": WIKI}})
        adf = '{"type":"doc","content":[{"type":"mediaSingle","content":[{"type":"media","attrs":{"id":"media-1","type":"file"}}]}]}'
        api.on("GET", f"{V2}/pages/10", {"id": "10", "body": {"atlas_doc_format": {"value": adf}}})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert "att2" in db.records and "att1" not in db.records
        assert db.records["att2"].parent_node_id == db.records["10"].id


class TestGroups:
    async def test_a_failed_member_read_does_not_empty_the_group(self, api, db, checkpoints) -> None:
        db.app_users.append(AppUser(
            app_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, source_user_id="acc-ana",
            org_id="org-1", email="ana@acme.com", full_name="Ana",
        ))
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        members = f"{V1}/group/grp-eng/membersByGroupId"
        api.on("GET", members, {"results": [{"accountId": "acc-ana", "email": "ana@acme.com"}], "size": 1})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        assert [m.email for m in db.user_groups[-1][1]] == ["ana@acme.com"]

        api.on("GET", members, json_response({"message": "busy"}, status=503))
        await connector._sync_user_groups()

        group, saved_members = db.user_groups[-1]
        assert group.source_user_group_id == "grp-eng"
        assert [m.email for m in saved_members] == ["ana@acme.com"]

    async def test_short_pages_that_say_more_follow_are_followed(self, api, db, checkpoints) -> None:
        for source_id, email in (("acc-ana", "ana@acme.com"), ("acc-bo", "bo@acme.com")):
            db.app_users.append(AppUser(
                app_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, source_user_id=source_id,
                org_id="org-1", email=email, full_name=email,
            ))

        def paged(first: list, second: list, path: str) -> Callable[[httpx.Request], httpx.Response]:
            def handler(request: httpx.Request) -> httpx.Response:
                if AtlassianApiStub.query(request).get("start") == "0":
                    return json_response({"results": first, "size": len(first), "_links": {"base": WIKI, "next": f"{path}?start=1"}})
                return json_response({"results": second, "size": len(second), "_links": {"base": WIKI}})
            return handler

        api.on("GET", f"{V1}/group", paged(
            [{"id": "grp-ops", "name": "ops"}], [{"id": "grp-eng", "name": "eng"}], "/rest/api/group",
        ))
        api.on("GET", f"{V1}/group/grp-ops/membersByGroupId", {"results": [], "size": 0, "_links": {"base": WIKI}})
        api.on("GET", f"{V1}/group/grp-eng/membersByGroupId", paged(
            [{"accountId": "acc-ana", "email": "ana@acme.com"}], [{"accountId": "acc-bo", "email": "bo@acme.com"}],
            "/rest/api/group/grp-eng/membersByGroupId",
        ))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_user_groups()

        saved = {g.source_user_group_id: sorted(m.email for m in members) for g, members in db.user_groups}
        assert saved == {"grp-ops": [], "grp-eng": ["ana@acme.com", "bo@acme.com"]}

    def _members_by_start(self, pages: dict[str, dict]) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(pages[AtlassianApiStub.query(request).get("start", "0")])
        return handler

    def _people(self, db) -> None:
        for source_id, email in (("acc-ana", "ana@acme.com"), ("acc-bo", "bo@acme.com"), ("acc-eve", "eve@acme.com")):
            db.app_users.append(AppUser(
                app_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, source_user_id=source_id,
                org_id="org-1", email=email, full_name=email,
            ))

    async def test_the_next_member_page_is_read_at_the_offset_its_link_gives(self, api, db, checkpoints) -> None:
        self._people(db)
        path = f"{V1}/group/grp-eng/membersByGroupId"
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        api.on("GET", path, self._members_by_start({
            "0": {"results": [{"accountId": "acc-ana", "email": "ana@acme.com"}], "size": 1,
                  "_links": {"base": WIKI, "next": "/rest/api/group/grp-eng/membersByGroupId?start=100&limit=100"}},
            "1": {"results": [{"accountId": "acc-eve", "email": "eve@acme.com"}], "size": 1, "_links": {"base": WIKI}},
            "100": {"results": [{"accountId": "acc-bo", "email": "bo@acme.com"}], "size": 1, "_links": {"base": WIKI}},
        }))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_user_groups()

        assert sorted(m.email for m in db.user_groups[-1][1]) == ["ana@acme.com", "bo@acme.com"]

    async def test_a_next_link_without_an_offset_keeps_the_stored_members(self, api, db, checkpoints) -> None:
        self._people(db)
        path = f"{V1}/group/grp-eng/membersByGroupId"
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        both = [{"accountId": "acc-ana", "email": "ana@acme.com"}, {"accountId": "acc-bo", "email": "bo@acme.com"}]
        api.on("GET", path, {"results": both, "size": 2, "_links": {"base": WIKI}})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        saves = len(db.user_groups)

        api.on("GET", path, self._members_by_start({
            "0": {"results": both[:1], "size": 1, "_links": {"base": WIKI, "next": "/rest/api/group/grp-eng/membersByGroupId?cursor=opaque"}},
            "1": {"results": [{"accountId": "acc-eve", "email": "eve@acme.com"}], "size": 1, "_links": {"base": WIKI}},
        }))
        await connector._sync_user_groups()

        assert len(db.user_groups) == saves, "the group is not saved from a guessed page"
        assert sorted(m.email for m in db.user_groups[-1][1]) == ["ana@acme.com", "bo@acme.com"]

    async def test_a_group_that_disappears_part_way_through_its_members_ends_up_empty(self, api, db, checkpoints) -> None:
        db.app_users.append(AppUser(
            app_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, source_user_id="acc-ana",
            org_id="org-1", email="ana@acme.com", full_name="Ana",
        ))
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        first_page = {"results": [{"accountId": "acc-ana", "email": "ana@acme.com"}] * 100, "size": 100}
        api.on("GET", f"{V1}/group/grp-eng/membersByGroupId", lambda r: json_response(
            first_page if AtlassianApiStub.query(r).get("start") == "0" else {"message": "no group"},
            status=200 if AtlassianApiStub.query(r).get("start") == "0" else 404,
        ))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_user_groups()

        group, saved_members = db.user_groups[-1]
        assert group.source_user_group_id == "grp-eng" and saved_members == [], "a deleted group keeps no members"


class TestRestrictedPageFiles:
    async def test_a_page_restricted_to_a_group_we_have_not_synced_is_not_opened(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10")])
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to(groups=("grp-new",)))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].inherit_permissions is False

    async def test_files_of_a_restricted_page_stay_restricted(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10", attachments=[SPEC])])
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].inherit_permissions is False
        assert db.records["att2"].inherit_permissions is False
        assert [p.email for p in db.record_permissions["att2"]] == ["ana@acme.com"]

    async def test_a_reindexed_file_of_a_restricted_page_stays_restricted(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10", attachments=[SPEC])])
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        api.on("GET", f"{V2}/attachments/att2", {**SPEC, "version": {"number": 2}, "_links": {"base": WIKI}})

        await connector.reindex_records([db.records["att2"]])

        (updated,) = db.content_updates
        assert updated.external_record_id == "att2" and updated.inherit_permissions is False

    async def test_a_reindexed_reply_on_a_restricted_page_stays_restricted(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10")])
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        reply = CommentRecord(
            org_id="org-1", record_name="Re: c1", record_type=RecordType.COMMENT, external_record_id="202",
            external_revision_id="1", connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, author_source_id="acc-ana",
            parent_external_record_id="201", parent_record_type=RecordType.COMMENT,
            external_record_group_id="77", parent_node_id=db.records["10"].id,
        )
        api.on("GET", f"{V2}/footer-comments/202", {
            "id": "202", "title": "Re: c1", "pageId": "10", "parentCommentId": "201",
            "version": {"number": 2, "authorId": "acc-ana", "createdAt": "2024-05-02T10:00:00.000Z"},
            "_links": {"base": WIKI, "webui": "/x/202"},
        })

        await connector.reindex_records([reply])

        (updated,) = db.content_updates
        assert updated.inherit_permissions is False, "a reply gets its page's restriction, not its parent comment's"
        assert (updated.parent_external_record_id, updated.parent_record_type) == ("201", RecordType.COMMENT)
        (update,) = db.permission_updates
        assert [p.email for p in update[1]] == ["ana@acme.com"]

    async def test_a_file_first_seen_while_opening_a_restricted_page_stays_restricted(self, api, db, checkpoints) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._process_page_attachments_for_children([SPEC], "10", "page-node-10", "77", None)

        assert db.records["att2"].inherit_permissions is False
        assert [p.email for p in db.record_permissions["att2"]] == ["ana@acme.com"]


class TestAuditLog:
    async def test_a_short_audit_page_that_says_more_follow_is_followed(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_permission_changes_from_audit_log()
        change = {"category": "Permissions", "associatedObjects": [
            {"objectType": "Page", "name": "Page 10"}, {"objectType": "Space", "name": "ENG"},
        ]}
        noise = {"category": "Users", "associatedObjects": []}

        def audit(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("start") == "0":
                return json_response({"results": [noise], "size": 1, "_links": {"base": WIKI, "next": "/rest/api/audit?start=1"}})
            return json_response({"results": [change], "size": 1, "_links": {"base": WIKI}})

        api.on("GET", f"{V1}/audit", audit)
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))

        await connector._sync_permission_changes_from_audit_log()

        assert db.records["10"].inherit_permissions is False, "the change on the second audit page is applied"

    async def test_every_page_of_the_title_search_is_read(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10"), v1_page("11")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_permission_changes_from_audit_log()
        change = {"category": "Permissions", "associatedObjects": [
            {"objectType": "Page", "name": "Page 10"}, {"objectType": "Space", "name": "ENG"},
        ]}
        api.on("GET", f"{V1}/audit", {"results": [change], "size": 1})
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        search.by_cursor = {None: search_page([v1_page("11")], cursor="T2"), "T2": search_page([v1_page("10")])}

        await connector._sync_permission_changes_from_audit_log()

        assert [q.get("cursor") for q in search.queries if q["cql"].startswith("title IN")] == [None, "T2"]
        assert db.records["10"].inherit_permissions is False, "the page on the search's second page gets its restriction"

    async def test_a_failed_second_page_of_the_title_search_keeps_the_audit_clock(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_permission_changes_from_audit_log()
        checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] = 1_000
        change = {"category": "Permissions", "associatedObjects": [
            {"objectType": "Page", "name": "Page 10"}, {"objectType": "Space", "name": "ENG"},
        ]}
        api.on("GET", f"{V1}/audit", {"results": [change], "size": 1})
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        others = [{**v1_page(f"9{i:02d}"), "title": "Page 10"} for i in range(200)]
        search.by_cursor = {None: search_page(others, cursor="T2"), "T2": json_response({"message": "busy"}, status=503)}

        with pytest.raises(ValueError):
            await connector._sync_permission_changes_from_audit_log()

        assert checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] == 1_000
        assert db.records["10"].inherit_permissions is True, "page 10 was on the page that failed; the next run reads it"

    async def test_a_failed_title_search_does_not_move_the_audit_clock(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_permission_changes_from_audit_log()
        checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] = 1_000
        change = {"category": "Permissions", "associatedObjects": [
            {"objectType": "Page", "name": "Page 10"}, {"objectType": "Space", "name": "ENG"},
        ]}
        api.on("GET", f"{V1}/audit", {"results": [change], "size": 1})
        search.by_cursor[None] = json_response({"message": "busy"}, status=503)

        with pytest.raises(ValueError):
            await connector._sync_permission_changes_from_audit_log()

        assert checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] == 1_000

    async def test_an_unreadable_audit_log_does_not_move_the_audit_clock(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_permission_changes_from_audit_log()
        checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] = 1_000
        api.on("GET", f"{V1}/audit", json_response({"message": "busy"}, status=503))

        await connector._sync_permission_changes_from_audit_log()

        assert checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] == 1_000

    async def test_a_page_restricted_later_takes_its_stored_files_with_it(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10", attachments=[SPEC])])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_permission_changes_from_audit_log()
        assert db.records["att2"].inherit_permissions is True

        change = {"category": "Permissions", "associatedObjects": [
            {"objectType": "Page", "name": "Page 10"}, {"objectType": "Space", "name": "ENG"},
        ]}
        api.on("GET", f"{V1}/audit", {"results": [change], "size": 1})
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        await connector._sync_permission_changes_from_audit_log()

        assert db.records["10"].inherit_permissions is False
        (file_update,) = [(r, perms) for r, perms in db.permission_updates if r.external_record_id == "att2"]
        assert file_update[0].inherit_permissions is False, "the page's file no longer inherits the space's access"
        assert [p.email for p in file_update[1]] == ["ana@acme.com"]

    async def test_a_folder_under_a_page_keeps_its_own_access_when_the_page_is_restricted(
        self, api, db, checkpoints, search
    ) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_permission_changes_from_audit_log()
        db.records["500"] = FileRecord(
            org_id="org-1", record_name="Specs", record_type=RecordType.FILE, external_record_id="500",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
            version=1, is_file=False, mime_type="text/directory", parent_external_record_id="10",
        )

        change = {"category": "Permissions", "associatedObjects": [
            {"objectType": "Page", "name": "Page 10"}, {"objectType": "Space", "name": "ENG"},
        ]}
        api.on("GET", f"{V1}/audit", {"results": [change], "size": 1})
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        await connector._sync_permission_changes_from_audit_log()

        assert db.records["10"].inherit_permissions is False
        assert not [r for r, _ in db.permission_updates if r.external_record_id == "500"], (
            "a folder has restrictions of its own and does not take the page's"
        )


class TestPlaceholderSweep:
    async def test_out_of_scope_ancestors_are_filled_in_without_indexing_their_content(self, api, db, checkpoints) -> None:
        page_stub = WebpageRecord(
            org_id="org-1", record_name="10", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="10",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, is_placeholder=True,
        )
        folder_stub = FileRecord(
            org_id="org-1", record_name="500", record_type=RecordType.FILE, external_record_id="500",
            connector_name=page_stub.connector_name, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR, version=0,
            is_file=False, mime_type=MimeTypes.FOLDER.value, is_placeholder=True,
        )
        gone_stub = page_stub.model_copy(update={"id": "gone-id", "external_record_id": "404"})
        db.placeholders = [page_stub, gone_stub]
        db.records["500"] = folder_stub
        api.on("GET", f"{V2}/pages/10", {
            "id": "10", "title": "Design doc", "spaceId": "77", "parentId": "500", "parentType": "folder",
            "version": {"number": 4, "createdAt": "2024-05-01T10:00:00.000Z"}, "_links": {"base": WIKI, "webui": "/x/10"},
        })
        api.on("GET", f"{V2}/folders/500", {
            "id": "500", "title": "Specs", "spaceId": "77", "version": {"number": 1}, "_links": {"base": WIKI, "webui": "/x/500"},
        })
        db.add_user("acc-ana", "ana@acme.com")
        api.on("GET", f"{V1}/content/500/restriction", {"results": [
            {"operation": "read", "restrictions": {"user": {"results": [{"accountId": "acc-ana"}]}, "group": {"results": []}}},
        ]})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sweep_placeholder_records("org-1")

        page, folder, gone = db.records["10"], db.records["500"], db.records["404"]
        assert page.record_name == "Design doc" and page.is_placeholder is True, "content stays unindexed"
        assert page.id == page_stub.id
        assert page.parent_external_record_id == "500" and page.parent_record_type == RecordType.FILE
        assert folder.record_name == "Specs" and folder.is_placeholder is False
        assert folder.inherit_permissions is False
        assert [p.email for p in db.record_permissions["500"]] == ["ana@acme.com"]
        assert gone.is_placeholder is True and db.record_permissions["404"] == [], "an unreachable ancestor fails closed"
        assert api.calls("GET", f"{V2}/folders/500"), "the folder was fetched via the folder API"

    async def test_a_chain_of_folder_ancestors_is_named_in_one_sweep(self, api, db, checkpoints) -> None:
        page_stub = WebpageRecord(
            org_id="org-1", record_name="10", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="10",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, is_placeholder=True,
        )
        db.placeholders = [page_stub]
        for folder_id in ("500", "600"):
            db.records[folder_id] = FileRecord(
                org_id="org-1", record_name=folder_id, record_type=RecordType.FILE, external_record_id=folder_id,
                connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
                version=0, is_file=False, mime_type=MimeTypes.FOLDER.value, is_placeholder=True,
            )
        api.on("GET", f"{V2}/pages/10", {
            "id": "10", "title": "Design doc", "spaceId": "77", "parentId": "500", "parentType": "folder",
            "version": {"number": 4, "createdAt": "2024-05-01T10:00:00.000Z"}, "_links": {"base": WIKI, "webui": "/x/10"},
        })
        api.on("GET", f"{V2}/folders/500", {
            "id": "500", "title": "Specs", "spaceId": "77", "parentId": "600", "parentType": "folder",
            "version": {"number": 1}, "_links": {"base": WIKI, "webui": "/x/500"},
        })
        api.on("GET", f"{V2}/folders/600", {
            "id": "600", "title": "Engineering", "spaceId": "77", "version": {"number": 1},
            "_links": {"base": WIKI, "webui": "/x/600"},
        })
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sweep_placeholder_records("org-1")

        assert db.records["500"].record_name == "Specs" and db.records["500"].is_placeholder is False
        assert db.records["600"].record_name == "Engineering" and db.records["600"].is_placeholder is False
        assert api.calls("GET", f"{V2}/folders/600"), "the second-level folder was fetched via the folder API"


class TestStreamingLegacyHtmlPages:
    async def test_page_images_are_inlined_through_the_attachment_api(self, api, db, checkpoints) -> None:
        html = (
            '<p><img src="/wiki/download/attachments/10/shot.png?version=1" data-linked-resource-id="321" '
            'data-linked-resource-type="attachment"></p>'
            '<p><img src="/wiki/download/thumbnails/10/spec.pdf" data-linked-resource-id="322" '
            'data-linked-resource-type="attachment"></p>'
            '<p><img src="/wiki/images/icons/emoticons/smile.svg"></p>'
        )
        api.on("GET", f"{V2}/pages/10", {"id": "10", "title": "Runbook", "body": {"styled_view": {"value": html}}, "_links": {"base": WIKI}})
        api.on("GET", f"{V2}/attachments/att321", {"id": "att321", "title": "shot.png", "mediaType": "image/png"})
        api.on("GET", f"{V2}/attachments/att322", {"id": "att322", "title": "spec.pdf", "mediaType": "application/pdf"})
        api.on("GET", f"{V1}/content/10/child/attachment/att321/download", httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"}))
        connector, _ = await ready_connector(db, checkpoints)

        out = await connector._fetch_page_content("10", RecordType.CONFLUENCE_PAGE)

        assert out.startswith("<h1>Runbook</h1>")
        assert "data:image/png;base64,UE5HREFUQQ==" in out
        assert "/wiki/download/thumbnails/10/spec.pdf" in out
        assert not api.calls("GET", f"{V1}/content/10/child/attachment/att322/download"), "a PDF thumbnail is not downloaded"

    async def test_a_page_the_token_cannot_read_is_not_reported_as_deleted(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V2}/pages/10", json_response({"message": "forbidden"}, status=403))
        connector, _ = await ready_connector(db, checkpoints)

        with pytest.raises(HTTPException) as err:
            await connector._fetch_page_content("10", RecordType.CONFLUENCE_PAGE)

        assert err.value.status_code != 404

    async def test_a_placeholder_cannot_be_opened(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        stub = WebpageRecord(
            org_id="org-1", record_name="x", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="10",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, is_placeholder=True,
        )
        with pytest.raises(HTTPException):
            await connector.stream_record(stub)
        assert not api.calls("GET", f"{V2}/pages/10")


def space_group(space_id: str, key: str) -> RecordGroup:
    return RecordGroup(
        name=key, short_name=key, external_group_id=space_id, group_type=RecordGroupType.CONFLUENCE_SPACES,
        connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
    )


ENG = space_group("77", "ENG")
PAGES_CHECKPOINT = "confluence_pages/ENG"
OLD_CHECKPOINT = "2020-01-01T00:00:00.000Z"


def in_space(*ids: str, archived: tuple[str, ...] = (), cursor: str | None = None) -> dict[str, Any]:
    """A v2 space listing: what the account can see, current unless named archived."""
    links: dict[str, Any] = {"base": WIKI}
    if cursor:
        links["next"] = f"/wiki/api/v2/spaces/77/pages?cursor={cursor}&limit=250"
    items = [{"id": i, "status": "archived" if i in archived else "current", "title": f"Page {i}"} for i in ids]
    return {"results": items, "_links": links}


class TestContentGoneFromSource:
    """What the account can no longer find, or a narrowed filter leaves out, is removed; a failed read or delete removes nothing and holds the checkpoint."""

    async def _two_pages_synced(self, api, db, checkpoints, search) -> ConfluenceConnector:
        search.folders = []
        search.blogposts = []
        search.by_cursor = {None: search_page([v1_page("10"), v1_page("11", attachments=[SPEC])])}
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10", "11"))
        api.on("GET", f"{V2}/spaces/77/blogposts", in_space())
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_spaces_content([ENG])
        assert {"10", "11", "att2"} <= set(db.records)
        assert self._checkpoint(checkpoints) is not None
        search.by_cursor = {None: search_page([v1_page("10", version=2)])}
        return connector

    def _hold_checkpoint_at_old_value(self, checkpoints) -> None:
        key = next(k for k in checkpoints.sync_points if k.endswith(PAGES_CHECKPOINT))
        checkpoints.sync_points[key]["last_sync_time"] = OLD_CHECKPOINT

    def _checkpoint(self, checkpoints) -> str | None:
        value = checkpoints.values_for(PAGES_CHECKPOINT)
        return value["last_sync_time"] if value else None

    def _refilter(self, connector, checkpoints, search, *excluded: str) -> None:
        checkpoints.sync_points.clear()  # saving filters deletes the connector's sync points
        connector.sync_filters = type(connector.sync_filters).from_dict(
            {"page_ids": {"operator": "not_in", "type": "list", "value": list(excluded)}} if excluded else {}
        )
        search.by_cursor = {None: search_page([v1_page("10")])}

    async def test_a_trashed_page_leaves_with_its_attachments_and_comments_and_its_folder_stays(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        db.records["900"] = FileRecord(
            org_id="org-1", record_name="Specs", record_type=RecordType.FILE, external_record_id="900",
            external_record_group_id="77", parent_external_record_id="11", parent_record_type=RecordType.CONFLUENCE_PAGE,
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
            version=0, is_file=False, mime_type=MimeTypes.FOLDER.value,
        )
        db.records["12"] = db.records["10"].model_copy(update={
            "id": "child-id", "external_record_id": "12", "parent_external_record_id": "11",
        })
        comment = CommentRecord(
            org_id="org-1", record_name="c", record_type=RecordType.COMMENT, external_record_id="c1",
            external_record_group_id="77", parent_external_record_id="11", parent_record_type=RecordType.WEBPAGE,
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
            version=0, author_source_id="acc-ana",
        )
        db.records["c1"] = comment
        db.records["c2"] = comment.model_copy(update={"id": "reply-id", "external_record_id": "c2",
                                                      "parent_external_record_id": "c1"})
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10", "12"))

        await connector._sync_spaces_content([ENG])

        assert not {"11", "att2", "c1", "c2"} & set(db.records), "the page goes with its attachment and comments"
        assert {"10", "900", "12"} <= set(db.records), "the folder and the child page stay"
        assert db.records["900"].parent_external_record_id is None, "the folder moves to the space root"
        assert db.records["12"].parent_external_record_id is None
        request = api.calls("GET", f"{V2}/spaces/77/pages")[-1]
        assert "status" not in AtlassianApiStub.query(request), "the default lists current and archived pages"

    async def test_a_trashed_page_and_its_trashed_comment_the_source_no_longer_has_are_removed(
        self, api, db, checkpoints, search
    ) -> None:
        """The scans list the trash, so a record already in it does not stay behind for good."""
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        db.records["c1"] = CommentRecord(
            org_id="org-1", record_name="c", record_type=RecordType.COMMENT, external_record_id="c1",
            external_record_group_id="77", parent_external_record_id="11", parent_record_type=RecordType.WEBPAGE,
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
            version=0, author_source_id="acc-ana", is_deleted=True,
        )
        db.records["11"].is_deleted = True
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))

        await connector._sync_spaces_content([ENG])

        assert not {"11", "att2", "c1"} & set(db.records)
        assert "10" in db.records

    async def test_a_page_the_account_can_no_longer_see_is_removed_and_returns_when_visible_again(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))

        await connector._sync_spaces_content([ENG])
        assert "11" not in db.records and "att2" not in db.records

        # Regaining access changes nothing Confluence dates: the change search still leaves it out.
        search.by_cursor = {None: search_page([])}
        search.indexed = {"10": v1_page("10"), "11": v1_page("11")}
        search.queries.clear()
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10", "11"))
        await connector._sync_spaces_content([ENG])
        assert "11" in db.records
        by_id = [q["cql"] for q in search.queries if "id in (11)" in q["cql"]]
        assert by_id and all("lastModified >" not in cql for cql in by_id), (
            "the listed-but-missing page is asked for by id, whatever its last modified time"
        )

    async def test_a_listed_page_the_filters_leave_out_is_not_brought_back(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "11")
        search.indexed = {"10": v1_page("10"), "11": v1_page("11")}
        await connector._sync_spaces_content([ENG])
        assert "11" not in db.records

        search.by_cursor = {None: search_page([])}
        await connector._sync_spaces_content([ENG])
        assert "11" not in db.records, "the id search runs with the filters, so an excluded page stays out"

    async def test_a_failed_search_for_a_listed_page_holds_the_checkpoint(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))
        await connector._sync_spaces_content([ENG])
        self._hold_checkpoint_at_old_value(checkpoints)

        api.on("GET", f"{V2}/spaces/77/pages", in_space("10", "11"))
        search.indexed = json_response({"message": "Service Unavailable"}, status=503)
        await connector._sync_spaces_content([ENG])
        assert "11" not in db.records
        assert self._checkpoint(checkpoints) == OLD_CHECKPOINT

    async def test_an_archived_page_the_account_can_see_is_kept(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10", "11", archived=("11",)))

        await connector._sync_spaces_content([ENG])
        assert "11" in db.records

        self._refilter(connector, checkpoints, search, "99")
        search.indexed = {"10": v1_page("10")}  # search never returns archived content
        await connector._sync_spaces_content([ENG])
        assert "11" in db.records, "a full sync keeps it too"

    async def test_a_failed_delete_holds_the_checkpoint_and_the_next_incremental_sync_finishes_it(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._hold_checkpoint_at_old_value(checkpoints)
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))
        db.fail_delete_for = {db.records["11"].id}

        await connector._sync_spaces_content([ENG])
        assert "11" in db.records, "the delete failed once"
        assert self._checkpoint(checkpoints) == OLD_CHECKPOINT

        await connector._sync_spaces_content([ENG])
        assert "11" not in db.records and "att2" not in db.records
        assert self._checkpoint(checkpoints) != OLD_CHECKPOINT

    @pytest.mark.parametrize(
        "listing",
        [
            json_response({"message": "busy"}, status=503),
            json_response({"message": "Something went wrong"}),
            [json_response(in_space("10", cursor="P2")), json_response(in_space(cursor="P3"))],
            [json_response(in_space("10", cursor="P2")), json_response(in_space("10", cursor="P2"))],
            [json_response(in_space("10", cursor="P2")), json_response({"message": "busy"}, status=503)],
        ],
        ids=["fails", "error-body-with-200", "empty-page-with-a-next-link", "cursor-comes-round-again", "later-page-fails"],
    )
    async def test_a_space_listing_not_read_to_the_end_removes_nothing_and_holds_the_checkpoint(
        self, api, db, checkpoints, search, listing
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._hold_checkpoint_at_old_value(checkpoints)
        api.on("GET", f"{V2}/spaces/77/pages", listing)

        await connector._sync_spaces_content([ENG])

        assert {"11", "att2"} <= set(db.records)
        assert self._checkpoint(checkpoints) == OLD_CHECKPOINT

    async def test_a_short_space_listing_page_is_followed_to_the_end(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/spaces/77/pages", [json_response(in_space("10", cursor="P2")), json_response(in_space("11"))])

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records

    async def test_an_unreadable_graph_removes_nothing_and_holds_the_checkpoint(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._hold_checkpoint_at_old_value(checkpoints)
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))
        db.fail_record_scan = True

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records and self._checkpoint(checkpoints) == OLD_CHECKPOINT

    async def test_a_failed_change_search_holds_the_checkpoint(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._hold_checkpoint_at_old_value(checkpoints)
        search.by_cursor = {None: json_response({"message": "busy"}, status=503)}
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))

        await connector._sync_spaces_content([ENG])

        assert "11" not in db.records, "the space listing is evidence on its own"
        assert self._checkpoint(checkpoints) == OLD_CHECKPOINT

    async def test_a_page_a_narrowed_filter_leaves_out_is_removed_by_the_full_sync_that_follows(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "11")
        search.indexed = {"10": v1_page("10"), "11": v1_page("11")}

        await connector._sync_spaces_content([ENG])

        assert "10" in db.records
        assert "11" not in db.records and "att2" not in db.records
        assert self._checkpoint(checkpoints) is not None

    async def test_a_page_a_lagging_search_misses_is_kept_and_the_full_sync_stays_owed(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "99")
        search.indexed = {"10": v1_page("10")}

        await connector._sync_spaces_content([ENG])

        assert {"10", "11", "att2"} <= set(db.records)
        assert self._checkpoint(checkpoints) is None

    async def test_a_page_the_filtered_search_still_finds_is_kept(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "99")
        search.indexed = {"10": v1_page("10"), "11": v1_page("11")}

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records, "the listing skipped it, the filter did not exclude it"
        assert self._checkpoint(checkpoints) is None

    async def test_with_no_content_filter_a_full_sync_never_removes_what_the_listing_skipped(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search)
        search.indexed = {"10": v1_page("10"), "11": v1_page("11")}

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records
        assert not any(re.search(r"(?:^| AND )id in \(", q.get("cql", "")) for q in search.queries)

    @pytest.mark.parametrize("failure", ["search", "graph"])
    async def test_a_failed_filter_check_removes_nothing_and_keeps_the_full_sync_owed(
        self, api, db, checkpoints, search, failure
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "11")
        if failure == "search":
            search.indexed = json_response({"message": "busy"}, status=503)
        else:
            search.indexed = {"11": v1_page("11")}
            db.fail_record_scan = True

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records
        assert self._checkpoint(checkpoints) is None, "the next sync is a full one again"

    @pytest.mark.parametrize(
        "next_page",
        [
            json_response({"message": "Service Unavailable"}, status=503),
            json_response({"message": "oops"}),
            json_response(search_page([], cursor="C3")),
        ],
        ids=["fails", "error-body-with-200", "empty-page-with-a-next-link"],
    )
    async def test_a_full_listing_not_read_to_the_end_removes_nothing_it_missed(
        self, api, db, checkpoints, search, next_page
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "11")
        search.by_cursor = {None: search_page([v1_page("10")], cursor="C2"), "C2": next_page}
        search.indexed = {"11": v1_page("11")}

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records and self._checkpoint(checkpoints) is None

    async def test_a_listed_page_that_fails_to_save_is_not_removed(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "99")
        search.by_cursor = {None: search_page([v1_page("10"), v1_page("11")])}
        search.indexed = {"10": v1_page("10")}
        db.fail_lookup_for = {"11"}

        await connector._sync_spaces_content([ENG])

        db.fail_lookup_for = set()
        assert "11" in db.records

    async def test_placeholder_ancestors_are_kept(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        db.records["5"] = WebpageRecord(
            org_id="org-1", record_name="5", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="5",
            external_record_group_id="77", connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, is_placeholder=True,
        )

        await connector._sync_spaces_content([ENG])

        assert "5" in db.records, "a stub is never listed, so its absence means nothing"


class TestSpacesOutOfScope:
    async def _synced_in(self, db, checkpoints, search) -> ConfluenceConnector:
        search.by_cursor = {None: search_page([v1_page("770", space_id=77), v1_page("990", space_id=99)])}
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_content("HR", RecordType.CONFLUENCE_PAGE)
        db.record_groups["77"] = ENG
        db.record_groups["99"] = space_group("99", "HR")
        assert checkpoints.values_for("confluence_pages/HR")["last_sync_time"]
        return connector

    def _spaces(self, api, *spaces: tuple[str, str]) -> None:
        api.on("GET", f"{V2}/spaces", {"results": [{"id": i, "key": k, "name": k} for i, k in spaces], "_links": {"base": WIKI}})
        api.on_suffix("GET", "/permissions", {"results": []})

    @pytest.mark.parametrize(
        "space_filter",
        [None, {"operator": "in", "type": "list", "value": ["ENG"]}],
        ids=["account-lost-access", "space-filter-leaves-it-out"],
    )
    async def test_a_space_no_longer_listed_is_removed_once_with_its_checkpoints_cleared(
        self, api, db, checkpoints, search, space_filter
    ) -> None:
        connector = await self._synced_in(db, checkpoints, search)
        if space_filter:
            connector.sync_filters = type(connector.sync_filters).from_dict({"space_keys": space_filter})
        self._spaces(api, ("77", "ENG"))

        spaces = await connector._sync_spaces()
        await connector._remove_spaces_out_of_scope(spaces)

        assert "770" in db.records and "990" not in db.records
        assert "99" not in db.record_groups
        assert not checkpoints.values_for("confluence_pages/HR")["last_sync_time"], "re-adding HR reads it in full"
        assert checkpoints.values_for("confluence_pages/ENG")["last_sync_time"]
        assert checkpoints.values_for("confluence_space_scope/all")["space_ids"] == ["77"]

        db.records["991"] = db.records["770"].model_copy(update={"id": "x991", "external_record_id": "991",
                                                                  "external_record_group_id": "99"})
        await connector._remove_spaces_out_of_scope(spaces)
        assert "991" in db.records, "an unchanged scope is not rescanned"

    @pytest.mark.parametrize(
        "listing",
        [
            json_response({"message": "busy"}, status=503),
            json_response({"message": "Something went wrong"}),
            [json_response({"results": [{"id": "77", "key": "ENG", "name": "ENG"}],
                            "_links": {"base": WIKI, "next": f"{V2}/spaces?cursor=S2&limit=20"}}),
             json_response({"message": "busy"}, status=503)],
            [json_response({"results": [{"id": "77", "key": "ENG", "name": "ENG"}],
                            "_links": {"base": WIKI, "next": f"{V2}/spaces?cursor=S2&limit=20"}}),
             json_response({"results": [], "_links": {"base": WIKI, "next": f"{V2}/spaces?cursor=S3&limit=20"}})],
        ],
        ids=["transient-failure", "error-body-with-200", "later-page-fails", "empty-page-with-a-next-link"],
    )
    async def test_a_space_listing_not_read_to_the_end_removes_nothing(
        self, api, db, checkpoints, search, listing
    ) -> None:
        connector = await self._synced_in(db, checkpoints, search)
        api.on("GET", f"{V2}/spaces", listing)
        api.on_suffix("GET", "/permissions", {"results": []})

        spaces = await connector._sync_spaces()
        await connector._remove_spaces_out_of_scope(spaces)

        assert connector._space_listing_complete is False
        assert {"770", "990"} <= set(db.records) and db.deleted == []
        assert checkpoints.values_for("confluence_pages/HR")["last_sync_time"]

    async def test_a_space_no_longer_listed_takes_its_trashed_records_with_it(self, api, db, checkpoints, search) -> None:
        connector = await self._synced_in(db, checkpoints, search)
        db.records["990"].is_deleted = True
        self._spaces(api, ("77", "ENG"))

        spaces = await connector._sync_spaces()
        await connector._remove_spaces_out_of_scope(spaces)

        assert "990" not in db.records
        assert "99" not in db.record_groups
        scope = checkpoints.values_for("confluence_space_scope/all")
        assert (scope["space_ids"], scope["pending"]) == (["77"], []), "the removal finished"

    async def test_a_failed_delete_keeps_the_space_and_its_checkpoints_until_the_next_sync(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._synced_in(db, checkpoints, search)
        self._spaces(api, ("77", "ENG"))
        db.fail_delete_for = {db.records["990"].id}

        spaces = await connector._sync_spaces()
        await connector._remove_spaces_out_of_scope(spaces)
        assert "990" in db.records and "99" in db.record_groups
        assert checkpoints.values_for("confluence_pages/HR")["last_sync_time"]
        scope = checkpoints.values_for("confluence_space_scope/all")
        assert "space_ids" not in scope and scope["pending"] == ["99"]

        await connector._remove_spaces_out_of_scope(spaces)
        assert "990" not in db.records and "99" not in db.record_groups
        assert checkpoints.values_for("confluence_space_scope/all")["pending"] == []

    async def test_a_space_whose_records_went_but_not_the_space_is_finished_next_sync(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._synced_in(db, checkpoints, search)
        self._spaces(api, ("77", "ENG"))
        real_delete = db.on_record_group_deleted
        db.on_record_group_deleted = AsyncMock(return_value=False)

        spaces = await connector._sync_spaces()
        await connector._remove_spaces_out_of_scope(spaces)
        assert "990" not in db.records and "99" in db.record_groups, "its records went, the space did not"

        db.on_record_group_deleted = real_delete
        await connector._remove_spaces_out_of_scope(spaces)
        assert "99" not in db.record_groups, "the pending space is finished though no record points at it"

    async def test_an_empty_space_listing_removes_nothing(self, api, db, checkpoints, search) -> None:
        connector = await self._synced_in(db, checkpoints, search)
        self._spaces(api)

        spaces = await connector._sync_spaces()
        await connector._remove_spaces_out_of_scope(spaces)

        assert "990" in db.records and "99" in db.record_groups

    async def test_a_failed_graph_read_removes_nothing(self, api, db, checkpoints, search) -> None:
        connector = await self._synced_in(db, checkpoints, search)
        self._spaces(api, ("77", "ENG"))
        db.fail_record_scan = True

        spaces = await connector._sync_spaces()
        await connector._remove_spaces_out_of_scope(spaces)

        assert "990" in db.records and checkpoints.values_for("confluence_space_scope/all") is None

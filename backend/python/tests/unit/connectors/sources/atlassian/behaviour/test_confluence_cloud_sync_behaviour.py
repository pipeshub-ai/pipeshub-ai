"""Confluence Cloud connector, driven over a fake Atlassian API with its real OAuth client.

The connector, the Confluence client and request builder, and the HTTP client
are real. Every HTTP request (including the one-off site lookup on
api.atlassian.com) is answered by an in-memory stub, and our databases are
in-memory fakes.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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

from app.config.constants.arangodb import (
    AccessRule,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.connectors.sources.atlassian.core.quota import (
    AtlassianQuotaExhausted,
    QuotaAwarePolicy,
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


@pytest.fixture(autouse=True)
def _fresh_notice_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """What was already noticed is kept per connector id across instances; no test sees another's."""
    monkeypatch.setattr(ConfluenceConnector, "_persistent_noticed", {})
    monkeypatch.setattr(ConfluenceConnector, "_quota_noticed_reset", {})


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


PAGE_TIME = "2024-05-01T10:00:00.000Z"


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def cql_minute(iso: str, less_hours: int = 0) -> str:
    when = datetime.fromisoformat(iso.replace("Z", "+00:00")) - timedelta(hours=less_hours)
    return when.strftime("%Y-%m-%d %H:%M")


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
        self.comments: list[dict[str, Any]] | httpx.Response | None = None
        self.attachments: list[dict[str, Any]] = []
        self.indexed: dict[str, dict[str, Any]] | httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        self.queries.append(q)
        cql = q.get("cql", "")
        if self.folders is not None and cql.startswith("type=folder"):
            return json_response(search_page(self.folders))
        if self.blogposts is not None and cql.startswith("type=blogpost"):
            return json_response(search_page(self.blogposts))
        if cql.startswith("type=attachment"):
            return json_response(search_page(self.attachments))
        if cql.startswith("type=comment"):
            if isinstance(self.comments, httpx.Response):
                return self.comments
            return json_response(search_page(self.comments or []))
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
        with pytest.raises(HTTPException, match="is not connected"):
            await connector.test_connection_and_access()

    async def test_rejected_token_at_setup_is_reported_not_swallowed(self, api, db, checkpoints) -> None:
        api.on("GET", RESOURCES_PATH, json_response({"message": "Unauthorized"}, status=401))
        connector, _ = await make_connector(db, checkpoints)
        with pytest.raises(ConnectorInitError, match="Couldn't connect to Confluence"):
            await connector.init()
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
        assert (EntityType.GROUP, "acc-stranger", PermissionType.READ) in grants, "a hidden-email user keeps access via a pseudo-group"
        assert not any("grp-unknown" in str(g) for g in grants), "an unknown group gets nothing"
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

        since = cql_minute(checkpoint["last_sync_time"], less_hours=24)
        assert f'lastModified > "{since}"' in search.queries[0]["cql"], "the checkpoint, looked back a day"
        assert db.records["10"].id == first_id and db.records["10"].version == 1
        assert len([r for r in db.records.values() if isinstance(r, WebpageRecord)]) == 3, "no duplicates"

    async def test_date_filters_reach_the_search_as_set(self, api, db, checkpoints, search) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        connector.sync_filters = type(connector.sync_filters).from_dict({
            "modified": {"operator": "is_between", "type": "datetime", "value": {
                "start": epoch_ms(datetime(2024, 3, 1, 9, 30, tzinfo=timezone.utc)),
                "end": epoch_ms(datetime(2024, 6, 1, 18, 0, tzinfo=timezone.utc)),
            }},
            "created": {"operator": "is_between", "type": "datetime", "value": {
                "start": epoch_ms(datetime(2023, 1, 1, tzinfo=timezone.utc)),
                "end": epoch_ms(datetime(2024, 2, 1, 12, 0, tzinfo=timezone.utc)),
            }},
        })

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        cql = search.queries[0]["cql"]
        assert 'lastModified > "2024-03-01 09:30"' in cql
        assert 'lastModified < "2024-06-01 18:00"' in cql, "not a day early"
        assert 'created > "2023-01-01 00:00"' in cql
        assert 'created < "2024-02-01 12:00"' in cql, "not a day early"

    async def test_the_later_of_the_modified_filter_and_the_looked_back_checkpoint_bounds_the_search(
        self, api, db, checkpoints, search
    ) -> None:
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        checkpoint = checkpoints.values_for("confluence_pages/ENG")["last_sync_time"]
        checkpoint_at = datetime.fromisoformat(checkpoint.replace("Z", "+00:00"))

        inside_look_back = (checkpoint_at - timedelta(hours=12)).replace(second=0, microsecond=0)
        connector.sync_filters = type(connector.sync_filters).from_dict({
            "modified": {"operator": "is_after", "type": "datetime", "value": {"start": epoch_ms(inside_look_back)}},
        })
        search.queries.clear()
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        cql = search.queries[0]["cql"]
        assert f'lastModified > "{inside_look_back.strftime("%Y-%m-%d %H:%M")}"' in cql, "nothing from before the filter date"
        assert cql.count("lastModified >") == 1

        connector.sync_filters = type(connector.sync_filters).from_dict({
            "modified": {"operator": "is_after", "type": "datetime", "value": {
                "start": epoch_ms(datetime(2020, 1, 1, tzinfo=timezone.utc)),
            }},
        })
        checkpoint = checkpoints.values_for("confluence_pages/ENG")["last_sync_time"]
        search.queries.clear()
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        assert f'lastModified > "{cql_minute(checkpoint, less_hours=24)}"' in search.queries[0]["cql"]

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
        assert restricted.access_rule == AccessRule.RESTRICTED, "space access alone does not open it; a grant on the page is needed too"
        assert restricted.inherit_permissions is True, "a RESTRICTED page that does not inherit is hidden from everyone"
        grants = {(p.entity_type, p.email or p.external_id, p.type) for p in db.record_permissions["10"]}
        assert grants == {
            (EntityType.USER, "ana@acme.com", PermissionType.READ),
            (EntityType.GROUP, "acc-no-email", PermissionType.READ),
            (EntityType.GROUP, "grp-leads", PermissionType.READ),
        }
        assert [g.source_user_group_id for g, _ in db.user_groups] == ["acc-no-email"], "a stand-in group keeps the grant for a user without email"
        assert edit_only.access_rule == AccessRule.STRICT, "an edit-only restriction does not hide the page from space members"
        assert db.record_permissions["11"] == []

    async def test_a_failed_restriction_lookup_never_opens_a_page_to_the_whole_space(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10")])
        api.on("GET", f"{V1}/content/10/restriction", json_response({"message": "rate limited"}, status=429))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        page = db.records.get("10")
        assert page is None or page.access_rule == AccessRule.RESTRICTED

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

        saved = checkpoints.values_for("confluence_pages/ENG")
        assert not saved.get("last_sync_time"), "a full listing keeps no checkpoint until it ends"
        assert saved["full_resume_after"] == PAGE_TIME, "the next sync resumes after the saved batch"

    async def test_a_failed_folder_listing_page_does_not_move_the_folder_checkpoint(self, api, db, checkpoints, search) -> None:
        folder = {**v1_page("500"), "type": "folder", "title": "Specs"}
        search.by_cursor[None] = search_page([folder], cursor="C2")
        search.by_cursor["C2"] = json_response({"message": "Service Unavailable"}, status=503)
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_folders("ENG")

        assert "500" in db.records
        assert checkpoints.values_for("confluence_folders/ENG")["last_sync_time"] == PAGE_TIME, "only past the saved batch"

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

    async def test_an_image_too_big_to_show_inside_the_page_stays_a_file(self, api, db, checkpoints, search) -> None:
        poster = {"id": "att1", "title": "poster.png", "mediaType": "image/png", "fileId": "media-1",
                  "fileSize": 50 * 1024 * 1024, "version": {"number": 1}}
        search.by_cursor[None] = search_page([v1_page("10", attachments=[poster])])
        api.on("GET", f"{V2}/pages/10/attachments", {"results": [poster], "_links": {"base": WIKI}})
        adf = '{"type":"doc","content":[{"type":"mediaSingle","content":[{"type":"media","attrs":{"id":"media-1","type":"file"}}]}]}'
        api.on("GET", f"{V2}/pages/10", {"id": "10", "body": {"atlas_doc_format": {"value": adf}}})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert "att1" in db.records, "the page body leaves it out, so it is kept as a file"


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

    async def test_a_failed_user_lookup_does_not_empty_the_group(self, api, db, checkpoints) -> None:
        db.app_users.append(AppUser(
            app_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, source_user_id="acc-ana",
            org_id="org-1", email="ana@acme.com", full_name="Ana",
        ))
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        api.on("GET", f"{V1}/group/grp-eng/membersByGroupId",
               {"results": [{"accountId": "acc-ana", "email": "ana@acme.com"}], "size": 1})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        saves = len(db.user_groups)
        assert [m.email for m in db.user_groups[-1][1]] == ["ana@acme.com"]

        db.get_all_app_users = AsyncMock(side_effect=ConnectionError("database unavailable"))
        await connector._sync_user_groups()

        assert len(db.user_groups) == saves, "the group is not saved when its members could not be looked up"
        assert [m.email for m in db.user_groups[-1][1]] == ["ana@acme.com"]

    async def test_a_lookup_that_finds_no_users_still_empties_the_group(self, api, db, checkpoints) -> None:
        db.app_users.append(AppUser(
            app_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, source_user_id="acc-ana",
            org_id="org-1", email="ana@acme.com", full_name="Ana",
        ))
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        api.on("GET", f"{V1}/group/grp-eng/membersByGroupId",
               {"results": [{"accountId": "acc-ana", "email": "ana@acme.com"}], "size": 1})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        assert [m.email for m in db.user_groups[-1][1]] == ["ana@acme.com"]

        db.app_users.clear()
        await connector._sync_user_groups()

        group, saved_members = db.user_groups[-1]
        assert group.source_user_group_id == "grp-eng"
        assert saved_members == []

    async def test_a_failed_account_id_lookup_does_not_empty_the_group(self, api, db, checkpoints) -> None:
        # Confluence hides emails, so the members come back as account ids only.
        db.add_user("acc-ana", "ana@acme.com")
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        api.on("GET", f"{V1}/group/grp-eng/membersByGroupId", {"results": [{"accountId": "acc-ana"}], "size": 1})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        saves = len(db.user_groups)
        assert [m.email for m in db.user_groups[-1][1]] == ["ana@acme.com"]

        async def failing_read(
            source_user_id: str, connector_id: str, *, raise_on_error: bool = False
        ) -> None:
            # Like the graph providers: a failed query answers None unless asked to raise.
            if raise_on_error:
                raise ConnectionError("database unavailable")
            return None

        db.get_user_by_source_id = failing_read
        await connector._sync_user_groups()

        assert len(db.user_groups) == saves, "the group is not saved when its members could not be looked up"
        assert [m.email for m in db.user_groups[-1][1]] == ["ana@acme.com"]

    async def test_an_account_id_with_no_linked_user_is_left_off_the_group(self, api, db, checkpoints) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        api.on("GET", f"{V1}/group", {"results": [{"id": "grp-eng", "name": "eng"}], "size": 1})
        api.on("GET", f"{V1}/group/grp-eng/membersByGroupId",
               {"results": [{"accountId": "acc-ana"}, {"accountId": "acc-unknown"}], "size": 2})
        connector, _ = await ready_connector(db, checkpoints)

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

        assert db.records["10"].access_rule == AccessRule.RESTRICTED

    async def test_files_of_a_restricted_page_stay_restricted(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10", attachments=[SPEC])])
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].access_rule == AccessRule.RESTRICTED
        file = db.records["att2"]
        assert (file.inherit_permissions, file.parent_external_record_id) == (True, "10"), (
            "the file inherits from its page, never straight from the space"
        )
        assert db.record_permissions["att2"] == []

    async def test_a_reindexed_file_of_a_restricted_page_stays_restricted(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10", attachments=[SPEC])])
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        api.on("GET", f"{V2}/attachments/att2", {**SPEC, "version": {"number": 2}, "_links": {"base": WIKI}})

        await connector.reindex_records([db.records["att2"]])

        (updated,) = db.content_updates
        assert updated.external_record_id == "att2"
        assert (updated.inherit_permissions, updated.parent_external_record_id) == (True, "10"), (
            "it still hangs under its restricted page and inherits from nothing else"
        )
        (update,) = db.permission_updates
        assert update[1] == []

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
        assert updated.inherit_permissions is True
        assert (updated.parent_external_record_id, updated.parent_record_type) == ("201", RecordType.COMMENT)
        (update,) = db.permission_updates
        assert [p.email for p in update[1]] == []

    async def test_a_file_first_seen_while_opening_a_page_is_left_to_the_sync(self, api, db, checkpoints) -> None:
        """Opening a page creates no records: the sync stores files with the page's access and its filters."""
        connector, _ = await ready_connector(db, checkpoints)

        children = await connector._process_page_attachments_for_children([SPEC], "10", "page-node-10", "77", None)

        assert children == {} and "att2" not in db.records


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

        assert db.records["10"].access_rule == AccessRule.RESTRICTED, "the change on the second audit page is applied"

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
        assert db.records["10"].access_rule == AccessRule.RESTRICTED, "the page on the search's second page gets its restriction"

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

        await connector._sync_permission_changes_from_audit_log()

        assert checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] == 1_000
        assert db.records["10"].access_rule == AccessRule.STRICT, "page 10 was on the page that failed; the next run reads it"

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

        await connector._sync_permission_changes_from_audit_log()

        assert checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] == 1_000

    async def test_an_unreadable_audit_log_does_not_move_the_audit_clock(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_permission_changes_from_audit_log()
        checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] = 1_000
        api.on("GET", f"{V1}/audit", json_response({"message": "busy"}, status=503))

        await connector._sync_permission_changes_from_audit_log()

        assert checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] == 1_000

    @pytest.mark.parametrize(("status", "moves"), [(503, False), (404, True)])
    async def test_a_failing_audit_log_falls_back_to_rechecking_restrictions(
        self, api, db, checkpoints, status, moves
    ) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_permission_changes_from_audit_log()
        checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] = 1_000
        api.on("GET", f"{V1}/audit", json_response({"message": "no"}, status=status))
        connector._recheck_restrictions = AsyncMock(return_value=True)

        await connector._sync_permission_changes_from_audit_log([ENG])

        connector._recheck_restrictions.assert_awaited_once_with([ENG])
        assert (checkpoints.values_for("permissions/audit_log")["last_sync_time_ms"] != 1_000) is moves

    async def test_a_page_restricted_later_takes_its_stored_files_with_it(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([v1_page("10", attachments=[SPEC])])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._sync_permission_changes_from_audit_log()
        assert db.records["10"].access_rule == AccessRule.STRICT

        change = {"category": "Permissions", "associatedObjects": [
            {"objectType": "Page", "name": "Page 10"}, {"objectType": "Space", "name": "ENG"},
        ]}
        api.on("GET", f"{V1}/audit", {"results": [change], "size": 1})
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        await connector._sync_permission_changes_from_audit_log()

        assert db.records["10"].access_rule == AccessRule.RESTRICTED
        (file_update,) = [(r, perms) for r, perms in db.permission_updates if r.external_record_id == "att2"]
        assert (file_update[0].inherit_permissions, file_update[0].parent_external_record_id) == (True, "10"), (
            "the file keeps inheriting from its page, which is now restricted"
        )
        assert file_update[1] == []

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

        assert db.records["10"].access_rule == AccessRule.RESTRICTED
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

        page, folder = db.records["10"], db.records["500"]
        assert page.record_name == "Design doc" and page.is_placeholder is True, "content stays unindexed"
        assert page.id == page_stub.id
        assert page.parent_external_record_id == "500" and page.parent_record_type == RecordType.FILE
        assert folder.record_name == "Specs" and folder.is_placeholder is False
        assert folder.access_rule == AccessRule.RESTRICTED
        assert [p.email for p in db.record_permissions["500"]] == ["ana@acme.com"]
        assert "404" not in db.records and "gone-id" in db.kept_records, "an unreachable stub is kept as stored, not rewritten"
        assert api.calls("GET", f"{V2}/folders/500"), "the folder was fetched via the folder API"

    async def test_a_filled_in_stub_that_did_not_change_keeps_its_grants(self, api, db, checkpoints) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        page_stub = WebpageRecord(
            org_id="org-1", record_name="10", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="10",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, is_placeholder=True,
        )
        db.placeholders = [page_stub]
        api.on("GET", f"{V2}/pages/10", {
            "id": "10", "title": "Design doc", "spaceId": "77",
            "version": {"number": 4, "createdAt": "2024-05-01T10:00:00.000Z"}, "_links": {"base": WIKI, "webui": "/x/10"},
        })
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sweep_placeholder_records("org-1")
        assert [p.email for p in db.record_permissions["10"]] == ["ana@acme.com"]
        db.placeholders = [db.records["10"]]
        writes_before = len(db.record_batches)

        await connector._sweep_placeholder_records("org-1")

        assert len(db.record_batches) == writes_before, "an unchanged stub is not written again"
        assert [p.email for p in db.record_permissions["10"]] == ["ana@acme.com"]
        assert db.records["10"].id in db.kept_records, "a full sync keeps its edges"

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
# A held checkpoint still moves past the batches that were saved, to their last change.
HELD_AT_SAVED_BATCH = PAGE_TIME


def in_space(*ids: str, archived: tuple[str, ...] = (), cursor: str | None = None) -> dict[str, Any]:
    """A v2 space listing: what the account can see, current unless named archived."""
    links: dict[str, Any] = {"base": WIKI}
    if cursor:
        links["next"] = f"/wiki/api/v2/spaces/77/pages?cursor={cursor}&limit=250"
    items = [{"id": i, "status": "archived" if i in archived else "current", "title": f"Page {i}"} for i in ids]
    return {"results": items, "_links": links}


def in_space_under(parents: dict[str, str | None]) -> dict[str, Any]:
    """A v2 space listing of current pages with their parent ids."""
    items = [{"id": i, "status": "current", "title": f"Page {i}", **({"parentId": p} if p else {})}
             for i, p in parents.items()]
    return {"results": items, "_links": {"base": WIKI}}


class TestAttachmentsChangedWithoutThePage:
    """Uploading a file, or a new version of one, adds no page version."""

    async def test_a_file_added_to_an_unchanged_page_is_synced(self, api, db, checkpoints, search) -> None:
        search.folders = []
        search.blogposts = []
        search.by_cursor = {None: search_page([v1_page("10")])}
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))
        api.on("GET", f"{V2}/spaces/77/blogposts", in_space())
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_spaces_content([ENG])
        assert "att2" not in db.records
        checkpoints.values_for("confluence_attachments/ENG")["checked_at"] = "2024-01-01T00:00:00.000Z"

        search.by_cursor = {None: search_page([])}  # the page itself did not change
        search.attachments = [{"id": "att2", "type": "attachment", "version": {"when": "2024-06-01T10:00:00.000Z"},
                               "container": {"id": "10", "type": "page"}}]
        search.indexed = {"10": v1_page("10", attachments=[SPEC])}
        await connector._sync_spaces_content([ENG])

        assert "att2" in db.records
        assert checkpoints.values_for("confluence_attachments/ENG")["checked_at"] > "2024-01-01T00:00:00.000Z"


class TestMovedPages:
    """Moving a page need not change its version, so the listing by last change never brings it back."""

    async def _synced(self, api, db, checkpoints, search) -> ConfluenceConnector:
        search.folders = []
        search.blogposts = []
        search.by_cursor = {None: search_page([v1_page("10"), v1_page("11")])}
        api.on("GET", f"{V2}/spaces/77/pages", in_space_under({"10": None, "11": None}))
        api.on("GET", f"{V2}/spaces/77/blogposts", in_space())
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_spaces_content([ENG])
        search.by_cursor = {None: search_page([])}
        return connector

    @staticmethod
    def _reads_of(search, page_id: str) -> int:
        return sum(1 for q in search.queries if re.search(rf"id in \([^)]*\b{page_id}\b", q.get("cql", "")))

    async def test_a_page_moved_without_a_new_version_gets_its_new_parent(self, api, db, checkpoints, search) -> None:
        connector = await self._synced(api, db, checkpoints, search)
        search.indexed = {"11": {**v1_page("11"), "ancestors": [{"id": "10", "type": "page"}]}}
        api.on("GET", f"{V2}/spaces/77/pages", in_space_under({"10": None, "11": "10"}))

        await connector._sync_spaces_content([ENG])

        assert db.records["11"].parent_external_record_id == "10"

    async def test_a_parent_the_apis_name_differently_is_read_again_once(self, api, db, checkpoints, search) -> None:
        connector = await self._synced(api, db, checkpoints, search)
        search.indexed = {"11": v1_page("11")}  # its ancestors never name the listed parent
        api.on("GET", f"{V2}/spaces/77/pages", in_space_under({"10": None, "11": "10"}))

        await connector._sync_spaces_content([ENG])
        await connector._sync_spaces_content([ENG])

        assert self._reads_of(search, "11") == 1

    async def test_a_failed_listing_of_a_quiet_space_is_reported(self, api, db, checkpoints, search) -> None:
        connector = await self._synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/spaces/77/pages", lambda _: json_response({"message": "boom"}, status=500))

        await connector._sync_spaces_content([ENG])

        assert any("couldn't check or remove" in p for p in connector._sync_problems)


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
        return value.get("last_sync_time") if value else None

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
        api.on("GET", f"{V2}/folders/900", {"id": "900", "status": "current", "spaceId": "77", "title": "Specs"})

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

    async def test_a_page_the_filters_leave_out_is_searched_for_once_a_day(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search, "11")
        search.indexed = {"10": v1_page("10"), "11": v1_page("11")}
        await connector._sync_spaces_content([ENG])
        search.by_cursor = {None: search_page([])}
        await connector._sync_spaces_content([ENG])

        def searches_for_11() -> int:
            return sum(1 for q in search.queries if re.search(r"(?:^| AND )id in \([^)]*\b11\b", q.get("cql", "")))

        search.queries.clear()
        await connector._sync_spaces_content([ENG])
        assert searches_for_11() == 0

        key = next(k for k in checkpoints.sync_points if "unstored" in k and "CONFLUENCE_PAGE" in k)
        checkpoints.sync_points[key]["at"] -= 2 * 24 * 3600
        await connector._sync_spaces_content([ENG])
        assert searches_for_11() == 1

    async def test_a_failed_search_for_a_listed_page_holds_the_checkpoint(self, api, db, checkpoints, search) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/spaces/77/pages", in_space("10"))
        await connector._sync_spaces_content([ENG])
        self._hold_checkpoint_at_old_value(checkpoints)

        api.on("GET", f"{V2}/spaces/77/pages", in_space("10", "11"))
        search.indexed = json_response({"message": "Service Unavailable"}, status=503)
        await connector._sync_spaces_content([ENG])
        assert "11" not in db.records
        assert self._checkpoint(checkpoints) == HELD_AT_SAVED_BATCH

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
        assert self._checkpoint(checkpoints) == HELD_AT_SAVED_BATCH

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
        assert self._checkpoint(checkpoints) == HELD_AT_SAVED_BATCH

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

        assert "11" in db.records and self._checkpoint(checkpoints) == HELD_AT_SAVED_BATCH

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
        # Read again by id, it is seen, so the space settles and the full sync can finish.
        assert self._checkpoint(checkpoints) is not None

    async def test_with_no_content_filter_a_full_sync_reads_again_what_the_listing_skipped(
        self, api, db, checkpoints, search
    ) -> None:
        connector = await self._two_pages_synced(api, db, checkpoints, search)
        self._refilter(connector, checkpoints, search)
        search.indexed = {"10": v1_page("10"), "11": v1_page("11")}

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records
        assert any(re.search(r"(?:^| AND )id in \(11\)", q.get("cql", "")) for q in search.queries), "read again by id"

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

    async def test_an_empty_space_that_leaves_scope_is_removed(self, api, db, checkpoints, search) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        db.record_groups["77"] = ENG
        db.record_groups["55"] = space_group("55", "EMPTY")
        self._spaces(api, ("77", "ENG"), ("55", "EMPTY"))
        await connector._remove_spaces_out_of_scope(await connector._sync_spaces())

        self._spaces(api, ("77", "ENG"))
        await connector._remove_spaces_out_of_scope(await connector._sync_spaces())

        assert "55" not in db.record_groups, "a space with no records still goes"
        assert "77" in db.record_groups

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


class TestConnectionErrors:
    """Connection checks raise a message the admin can act on; the API shows it as is."""

    @pytest.mark.parametrize(
        ("status", "body", "says"),
        [
            (401, {"message": "Unauthorized"}, "rejected the credentials"),
            (403, {"message": "Forbidden"}, "can't read Confluence"),
            (404, {"message": "Not found"}, "No Confluence site was found"),
            (503, {"message": "busy"}, r"did not answer the connection test \(HTTP 503\)"),
        ],
    )
    async def test_a_failed_connection_test_says_why(self, api, db, checkpoints, status, body, says) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        api.on("GET", f"{V2}/spaces", json_response(body, status=status))

        with pytest.raises(ConnectorInitError, match=says):
            await connector.test_connection_and_access()

    async def test_a_missing_oauth_scope_is_named(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        granted = [s for s in AtlassianScope.get_confluence_read_access() if s != "read:audit-log:confluence"]
        api.on("GET", RESOURCES_PATH, json_response([{"id": CLOUD_ID, "name": "acme", "url": SITE, "scopes": granted}]))
        api.on(
            "GET", f"{V2}/spaces",
            json_response({"code": 401, "message": "Unauthorized; scope does not match"}, status=401),
        )

        with pytest.raises(ConnectorInitError, match="Missing: read:audit-log:confluence"):
            await connector.test_connection_and_access()

    async def test_an_unreachable_site_is_reported(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        api.on("GET", f"{V2}/spaces", refuse)

        with pytest.raises(ConnectorInitError, match="Couldn't reach Confluence"):
            await connector.test_connection_and_access()

    async def test_incomplete_settings_name_the_missing_one(self, api, db, checkpoints) -> None:
        config = {"auth": {"authType": "API_TOKEN", "email": "svc@acme.com", "apiToken": "tok"}, "filters": {}}
        connector, _ = await make_connector(db, checkpoints, config)

        with pytest.raises(ConnectorInitError, match="Base URL is required"):
            await connector.init()


class TestRetriesAndRateLimit:
    async def test_oauth_calls_are_paced_below_api_token_calls(self, api, db, checkpoints) -> None:
        oauth, _ = await ready_connector(db, checkpoints)
        token, _ = await ready_connector(db, checkpoints, {"auth": {
            "authType": "API_TOKEN", "baseUrl": SITE, "email": "svc@acme.com", "apiToken": "tok"}, "filters": {}})

        oauth_policy = oauth.external_client.get_client().resilience
        token_policy = token.external_client.get_client().resilience
        assert isinstance(oauth_policy, QuotaAwarePolicy) and oauth_policy.rate_limit == 6
        assert isinstance(token_policy, QuotaAwarePolicy) and token_policy.rate_limit == 10
        assert oauth_policy.max_retries == 3

    async def test_a_re_init_keeps_the_same_policy(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        first = connector.external_client.get_client().resilience

        assert await connector.init() is True

        assert connector.external_client.get_client().resilience is first, "a spent quota is not forgotten"

    def test_a_wait_longer_than_the_policy_allows_marks_the_quota_spent(self) -> None:
        policy = QuotaAwarePolicy(rate_limit=6, max_retries=3, max_delay=60.0)

        policy.note_response_status(429)
        assert policy.note_retry(0, 5.0) >= 5.0, "a short Retry-After is waited out"
        with pytest.raises(AtlassianQuotaExhausted):
            policy.note_retry(0, 1800.0)

        assert policy.quota_reset_at is not None

    async def test_a_spent_quota_fails_every_request_fast(self) -> None:
        policy = QuotaAwarePolicy(rate_limit=6, max_retries=3, max_delay=60.0)
        policy.quota_reset_at = time.time() + 600

        with pytest.raises(AtlassianQuotaExhausted, match="UTC"):
            await policy.acquire()

    async def test_a_spent_quota_ends_the_sync_with_one_notice_and_keeps_its_place(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        for step in ("_sync_users", "_sync_user_groups", "_reconcile_inactive_app_users",
                     "_remove_spaces_out_of_scope", "_sync_permission_changes_from_audit_log",
                     "_sweep_placeholder_records", "register_authenticated_source_user"):
            setattr(connector, step, AsyncMock())

        async def quota_runs_out() -> list:
            connector._client_policy.quota_reset_at = time.time() + 1800
            raise AtlassianQuotaExhausted(connector._client_policy.quota_reset_at)

        connector._sync_spaces = quota_runs_out

        await connector.run_sync()  # not raised: the next sync resumes

        (note,) = connector._notification_service.sent
        assert "quota used up" in note["title"]
        assert "Atlassian's hourly API quota" in note["message"] and "UTC" in note["message"]
        assert connector.full_sync_coverage().complete is False, "a full sync stopped early unlinks nothing"


class TestSyncProblemsNotice:
    """One notice per failing run, to the creator and the org admins; none for a clean run."""

    async def _run(self, connector: ConfluenceConnector) -> None:
        for step in (
            "_sync_users", "_sync_user_groups", "_reconcile_inactive_app_users",
            "_sync_spaces_content", "_remove_spaces_out_of_scope",
            "_sync_permission_changes_from_audit_log", "_sweep_placeholder_records",
            "register_authenticated_source_user",
        ):
            setattr(connector, step, AsyncMock())
        await connector.run_sync()

    async def test_a_failed_space_listing_is_reported_once_at_the_end(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V2}/spaces", json_response({"message": "busy"}, status=503))
        connector, _ = await ready_connector(db, checkpoints)

        await self._run(connector)

        (note,) = connector._notification_service.sent
        assert "Couldn't read the Confluence space list (HTTP 503)" in note["message"]
        assert note["recipient_user_ids"] == ["creator-1"]
        assert [r.value for r in note["recipient_roles"]] == ["admin"]
        assert "finished with problems" in note["title"]
        assert connector.full_sync_coverage().complete is False, "a full sync unlinks nothing it did not see"

    async def test_the_same_failure_is_reported_on_every_run(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V2}/spaces", json_response({"message": "busy"}, status=503))
        connector, _ = await ready_connector(db, checkpoints)

        await self._run(connector)
        await self._run(connector)

        assert len(connector._notification_service.sent) == 2

    async def test_a_clean_run_sends_nothing(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V2}/spaces", {"results": [], "_links": {"base": WIKI}})
        connector, _ = await ready_connector(db, checkpoints)

        await self._run(connector)

        assert connector._notification_service.sent == []
        assert connector.full_sync_coverage().complete is True

    async def test_a_sync_that_stops_says_so_and_still_raises(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        connector._sync_spaces = AsyncMock(side_effect=RuntimeError("graph store down"))

        with pytest.raises(RuntimeError):
            await self._run(connector)

        (note,) = connector._notification_service.sent
        assert note["title"].endswith("sync failed")
        assert "The sync stopped: graph store down" in note["message"]

    async def test_skipped_pages_are_counted_per_space(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10"), v1_page("11")])
        api.on_suffix("GET", "/restriction", json_response({"message": "busy"}, status=503))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._report_sync_problems()

        (note,) = connector._notification_service.sent
        assert "Space ENG: skipped 2 pages whose restrictions could not be read" in note["message"]

    async def test_a_condition_counts_as_noticed_only_after_a_finished_run(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        condition = "Couldn't read the Confluence audit log (HTTP 403)"

        async def run(finished: bool) -> int:
            connector._sync_problems = []
            connector._persistent_problems = set()
            connector._note_problem(condition, unread=False, persistent=True)
            connector._sync_finished = finished
            before = len(connector._notification_service.sent)
            await connector._report_sync_problems()
            return len(connector._notification_service.sent) - before

        assert await run(finished=False) == 1
        assert await run(finished=False) == 1, "a run that stopped early may not have reached the check"
        assert await run(finished=True) == 1
        assert await run(finished=True) == 0, "shown once per change"

    async def test_a_refused_audit_log_explains_what_it_needs(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V1}/audit", json_response({"message": "Forbidden"}, status=403))
        connector, _ = await ready_connector(db, checkpoints)

        assert await connector._fetch_permission_audit_logs(0, 1) is None
        await connector._report_sync_problems()

        (note,) = connector._notification_service.sent
        assert "audit log (HTTP 403)" in note["message"] and "administrator" in note["message"]
        assert connector.full_sync_coverage().complete is True, "a refused audit log leaves no content unread"


def page_changed_at(pid: str, when: str) -> dict[str, Any]:
    page = v1_page(pid)
    page["history"]["lastUpdated"]["when"] = when
    return page


class TestCheckpointPerBatch:
    """The checkpoint moves after each saved batch, never past an item that was not saved."""

    async def test_a_listing_cut_off_resumes_after_the_last_saved_batch(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page(
            [page_changed_at("10", "2024-05-01T10:00:00.000Z"), page_changed_at("11", "2024-05-02T09:30:00.000Z")],
            cursor="C2",
        )
        search.by_cursor["C2"] = json_response({"message": "busy"}, status=503)
        connector, _ = await ready_connector(db, checkpoints)

        listing = await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert listing.complete is False
        saved = checkpoints.values_for("confluence_pages/ENG")
        assert saved["full_resume_after"] == "2024-05-02T09:30:00.000Z"

        search.queries.clear()
        search.by_cursor = {None: search_page([page_changed_at("12", "2024-05-04T09:00:00.000Z")])}
        listing = await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert f'lastModified > "{cql_minute("2024-05-02T09:30:00.000Z", less_hours=24)}"' in search.queries[0]["cql"]
        assert listing.full and listing.resumed and listing.complete
        assert checkpoints.values_for("confluence_pages/ENG")["full_resume_after"] == "", "a finished listing clears it"

    async def test_the_checkpoint_never_moves_back(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([page_changed_at("10", "2024-05-01T10:00:00.000Z")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        now = checkpoints.values_for("confluence_pages/ENG")["last_sync_time"]

        search.by_cursor = {None: search_page([page_changed_at("10", "2024-05-01T10:00:00.000Z")], cursor="C2")}
        search.by_cursor["C2"] = json_response({"message": "busy"}, status=503)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert checkpoints.values_for("confluence_pages/ENG")["last_sync_time"] == now

    async def test_a_skipped_item_holds_the_checkpoint(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([page_changed_at("10", "2024-05-01T10:00:00.000Z")], cursor="C2")
        search.by_cursor["C2"] = search_page([page_changed_at("11", "2024-05-03T10:00:00.000Z")])
        api.on("GET", f"{V1}/content/10/restriction", json_response({"message": "busy"}, status=503))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert checkpoints.values_for("confluence_pages/ENG") is None, "page 10 was not saved, so nothing moves"
        assert "11" in db.records, "the rest of the listing is still synced"

    async def test_a_page_that_fails_to_process_holds_the_checkpoint(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([page_changed_at("10", "2024-05-01T10:00:00.000Z")])
        connector, _ = await ready_connector(db, checkpoints)
        connector._process_webpage_with_update = AsyncMock(side_effect=RuntimeError("bad page"))

        listing = await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert listing.complete is False
        assert checkpoints.values_for("confluence_pages/ENG") is None


class TestUsersListing:
    async def test_guests_and_external_collaborators_are_marked_as_guests(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V1}/search/user", [{"results": [
            {"user": {"accountId": "acc-1", "displayName": "Ana", "isGuest": False}, "email": "ana@acme.com"},
            {"user": {"accountId": "acc-2", "displayName": "Gus", "isGuest": True}, "email": "gus@else.com"},
            {"user": {"accountId": "acc-3", "displayName": "Eve", "isExternalCollaborator": True}, "email": "eve@else.com"},
        ], "_links": {"base": WIKI}}, {"results": []}])
        connector, _ = await ready_connector(db, checkpoints)
        connector._account_statuses_known = True

        await connector._sync_users()

        assert {u.source_user_id: u.is_guest for u in db.app_users} == {"acc-1": False, "acc-2": True, "acc-3": True}

    async def test_a_short_page_before_the_end_does_not_cut_the_user_list(self, api, db, checkpoints) -> None:
        def users(first: int, count: int) -> dict[str, Any]:
            return {"results": [{"user": {"accountId": f"acc-{i}", "displayName": f"User {i}"},
                                 "email": f"user{i}@acme.com"} for i in range(first, first + count)],
                    "_links": {"base": WIKI}}

        api.on("GET", f"{V1}/search/user", [users(0, 100), users(100, 40), users(140, 60), {"results": []}])
        connector, _ = await ready_connector(db, checkpoints)
        connector._account_statuses_known = True

        await connector._sync_users()

        assert len({u.source_user_id for u in db.app_users}) == 200
        assert [AtlassianApiStub.query(r)["start"] for r in api.calls("GET", f"{V1}/search/user")] == [
            "0", "100", "140", "200",
        ]


class TestUsersGoneFromConfluence:
    @staticmethod
    def _users(*ids: int) -> dict[str, Any]:
        return {"results": [{"user": {"accountId": f"acc-{i}", "displayName": f"User {i}"},
                             "email": f"user{i}@acme.com"} for i in ids],
                "_links": {"base": WIKI}}

    async def test_a_user_missing_from_a_complete_list_loses_access(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V1}/search/user", [self._users(1, 2, 3), {"results": []}])
        connector, _ = await ready_connector(db, checkpoints)
        connector._account_statuses_known = True
        await connector._sync_users()

        api.on("GET", f"{V1}/search/user", [self._users(1, 2), {"results": []}])
        await connector._sync_users()

        assert [u.source_user_id for u in db.withdrawn_users] == ["acc-3"]

    async def test_a_cut_short_list_withdraws_no_one(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V1}/search/user", [self._users(1, 2, 3), {"results": []}])
        connector, _ = await ready_connector(db, checkpoints)
        connector._account_statuses_known = True
        await connector._sync_users()

        api.on("GET", f"{V1}/search/user", [self._users(1), json_response({"message": "busy"}, status=503)])
        await connector._sync_users()

        assert db.withdrawn_users == []

    async def test_most_users_missing_is_not_trusted(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V1}/search/user", [self._users(1, 2, 3), {"results": []}])
        connector, _ = await ready_connector(db, checkpoints)
        connector._account_statuses_known = True
        await connector._sync_users()

        api.on("GET", f"{V1}/search/user", [self._users(1), {"results": []}])
        await connector._sync_users()

        assert db.withdrawn_users == []
        assert any("looks like a misread" in p for p in connector._sync_problems)


def restriction_event(object_type: str, title: str) -> dict[str, Any]:
    return {"results": [{"category": "Permissions", "summary": "Content restriction added",
                         "associatedObjects": [{"objectType": object_type, "name": title},
                                               {"objectType": "Space", "name": "Engineering"}]}],
            "_links": {"base": WIKI}}


class TestRestrictionChangesFromTheAuditLog:
    async def test_the_first_sync_opens_the_audit_window_when_it_starts(self, api, db, checkpoints, search) -> None:
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._open_audit_window()
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        # Restricted after the page was read, while the first sync still runs.
        api.on("GET", f"{V1}/audit", restriction_event("Page", "Page 10"))
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))

        await connector._sync_permission_changes_from_audit_log()

        assert api.calls("GET", f"{V1}/audit"), "the window opened at the start is read at the end"
        assert db.records["10"].access_rule == AccessRule.RESTRICTED

    async def test_a_folder_restriction_change_is_applied(self, api, db, checkpoints, search) -> None:
        folder = {**v1_page("500"), "type": "folder", "title": "Specs"}
        search.folders = [folder]
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_folders("ENG")
        assert db.records["500"].access_rule == AccessRule.STRICT
        await connector._open_audit_window()
        api.on("GET", f"{V1}/audit", restriction_event("Folder", "Specs"))
        api.on("GET", f"{V1}/content/500/restriction", read_restricted_to("acc-ana"))
        search.by_cursor[None] = search_page([folder])

        await connector._sync_permission_changes_from_audit_log()

        assert db.records["500"].access_rule == AccessRule.RESTRICTED


class TestBackgroundStartFailure:
    async def test_a_sync_that_cannot_start_notifies_the_creator_and_admins(self, api, db, checkpoints) -> None:
        api.on("GET", RESOURCES_PATH, json_response({"message": "Unauthorized"}, status=401))
        connector, _ = await make_connector(db, checkpoints)
        with pytest.raises(ConnectorInitError) as err:
            await connector.init()

        ConfluenceConnector._failed_start_noticed_at.clear()
        await connector.on_background_init_failed(err.value)
        await connector.on_background_init_failed(err.value)  # the same start, retried at once
        ConfluenceConnector._failed_start_noticed_at[CONNECTOR_ID] -= 3600  # the next scheduled run
        await connector.on_background_init_failed(err.value)

        notes = connector._notification_service.sent
        assert len(notes) == 2, "one notice per failed run, not per retry"
        assert notes[0]["title"].endswith("could not start")
        assert "Couldn't connect to Confluence" in notes[0]["message"]
        assert "Unauthorized" not in notes[0]["message"] and "401" not in notes[0]["message"], "no raw response text"
        assert notes[0]["recipient_user_ids"] == ["creator-1"]
        assert [r.value for r in notes[0]["recipient_roles"]] == ["admin"]


class TestLongRestrictionLists:
    async def test_pages_follow_the_page_size_confluence_used(self, api, db, checkpoints, search) -> None:
        for i in range(60):
            db.add_user(f"acc-{i}", f"user{i}@acme.com")

        def restriction(request: httpx.Request) -> httpx.Response:
            start = int(AtlassianApiStub.query(request).get("start", "0"))
            users = [{"accountId": f"acc-{i}"} for i in range(start, min(start + 25, 60))]
            return json_response({"results": [{"operation": "read", "restrictions": {
                "user": {"results": users, "start": start, "limit": 25, "size": len(users), "totalSize": 60},
                "group": {"results": [], "start": start, "limit": 25, "size": 0},
            }}]})

        api.on("GET", f"{V1}/content/10/restriction", restriction)
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert len({p.email for p in db.record_permissions["10"]}) == 60
        assert [AtlassianApiStub.query(r)["start"] for r in api.calls("GET", f"{V1}/content/10/restriction")] == [
            "0", "25", "50",
        ]

    async def test_a_read_restriction_naming_more_than_one_page_of_users_grants_them_all(
        self, api, db, checkpoints, search
    ) -> None:
        for i in range(150):
            db.add_user(f"acc-{i}", f"user{i}@acme.com")

        def restriction(request: httpx.Request) -> httpx.Response:
            start = int(AtlassianApiStub.query(request).get("start", "0"))
            users = [{"accountId": f"acc-{i}"} for i in range(start, min(start + 100, 150))]
            return json_response({"results": [{"operation": "read", "restrictions": {
                "user": {"results": users, "start": start, "limit": 100, "size": len(users)},
                "group": {"results": [], "start": start, "limit": 100, "size": 0},
            }}]})

        api.on("GET", f"{V1}/content/10/restriction", restriction)
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        granted = {p.email for p in db.record_permissions["10"]}
        assert len(granted) == 150, "users past the first 100 keep their access"
        assert [AtlassianApiStub.query(r)["start"] for r in api.calls("GET", f"{V1}/content/10/restriction")] == ["0", "100"]


class TestGroupsGoneFromConfluence:
    def _groups(self, api: AtlassianApiStub, *groups: tuple[str, str]) -> None:
        api.on("GET", f"{V1}/group", {"results": [{"id": gid, "name": name} for gid, name in groups],
                                     "_links": {"base": WIKI}})
        api.on_suffix("GET", "/membersByGroupId", {"results": [], "_links": {"base": WIKI}})

    async def test_a_group_deleted_in_confluence_is_removed_with_its_access(self, api, db, checkpoints) -> None:
        self._groups(api, ("g-eng", "engineering"), ("g-old", "old-team"))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        await connector._create_pseudo_group("acc-hidden")

        self._groups(api, ("g-eng", "engineering"))
        api.on("GET", f"{V1}/group/g-old/membersByGroupId", json_response({"message": "gone"}, status=404))
        await connector._sync_user_groups()

        assert db.deleted_groups == ["g-old"], "the pseudo-group and the listed group stay"

    async def test_a_failed_group_listing_removes_nothing(self, api, db, checkpoints) -> None:
        self._groups(api, ("g-eng", "engineering"), ("g-old", "old-team"))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()

        api.on("GET", f"{V1}/group", json_response({"message": "busy"}, status=503))
        await connector._sync_user_groups()

        assert db.deleted_groups == []


class TestListingsThatDoNotEnd:
    async def test_a_page_list_whose_next_link_repeats_stops_and_holds_the_checkpoint(
        self, api, db, checkpoints, search
    ) -> None:
        search.by_cursor[None] = search_page([v1_page("10")], cursor="C2")
        search.by_cursor["C2"] = search_page([v1_page("11")], cursor="C2")
        connector, _ = await ready_connector(db, checkpoints)

        listing = await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert listing.complete is False
        assert [q.get("cursor") for q in search.queries] == [None, "C2"], "C2 is not read twice"
        assert any("pointed back" in p for p in connector._sync_problems)

    async def test_an_empty_folder_page_that_points_further_is_a_failed_read(self, api, db, checkpoints, search) -> None:
        search.folders = []

        def folders(request: httpx.Request) -> httpx.Response:
            return json_response({"results": [], "_links": {"base": WIKI, "next": "/rest/api/content/search?cursor=F2"}})

        api.on("GET", f"{V1}/content/search", folders)
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_folders("ENG")

        assert checkpoints.values_for("confluence_folders/ENG") is None
        assert any("folder list stopped early" in p for p in connector._sync_problems)

    @pytest.mark.parametrize("next_page", [
        {"results": [], "_links": {"next": f"{V2}/spaces/1/permissions?cursor=P3"}},
        {"results": [{"principal": {"type": "user", "id": "acc-ana"}, "operation": {"key": "read", "targetType": "space"}}],
         "_links": {"next": f"{V2}/spaces/1/permissions?cursor=P2"}},
    ], ids=["empty-page-pointing-further", "next-link-repeats"])
    async def test_a_space_permission_list_read_in_part_keeps_the_stored_access(
        self, api, db, checkpoints, next_page
    ) -> None:
        first = {"results": [{"principal": {"type": "user", "id": "acc-ana"},
                              "operation": {"key": "read", "targetType": "space"}}],
                 "_links": {"next": f"{V2}/spaces/1/permissions?cursor=P2"}}

        def perms(request: httpx.Request) -> httpx.Response:
            return json_response(next_page if AtlassianApiStub.query(request).get("cursor") == "P2" else first)

        api.on("GET", f"{V2}/spaces/1/permissions", perms)
        connector, _ = await ready_connector(db, checkpoints)

        assert await connector._fetch_space_permissions("1", "Engineering") is None

    async def test_a_failed_group_read_keeps_the_stored_space_access(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V2}/spaces/1/permissions", lambda _: json_response({"results": [
            {"principal": {"type": "access-class", "id": "all-licensed-users"},
             "operation": {"key": "read", "targetType": "space"}},
        ], "_links": {}}))
        connector, _ = await ready_connector(db, checkpoints)
        db.fail_group_listing = True

        assert await connector._fetch_space_permissions("1", "Engineering") is None


class TestAttachmentsGoneFromAPage:
    def _page_with(self, api: AtlassianApiStub, search: ContentSearch, *ids: str, version: int = 1) -> None:
        atts = [{"id": i, "title": f"{i}.pdf", "mediaType": "application/pdf", "fileSize": 10,
                 "version": {"number": 1}, "_links": {"download": f"/download/attachments/10/{i}.pdf"}} for i in ids]
        search.by_cursor = {None: search_page([v1_page("10", version=version, attachments=atts)])}
        api.on("GET", f"{V2}/pages/10/attachments", {"results": atts, "_links": {"base": WIKI}})
        api.on("GET", f"{V2}/pages/10", {"id": "10", "body": {"atlas_doc_format": {"value": '{"type":"doc","content":[]}'}}})

    async def test_an_attachment_removed_from_a_page_is_removed(self, api, db, checkpoints, search) -> None:
        self._page_with(api, search, "att1", "att2")
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        assert {"att1", "att2"} <= set(db.records)

        self._page_with(api, search, "att1", version=2)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert "att1" in db.records and "att2" not in db.records

    async def test_the_last_attachment_removed_is_removed_too(self, api, db, checkpoints, search) -> None:
        self._page_with(api, search, "att1")
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        self._page_with(api, search, version=2)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert "att1" not in db.records and "10" in db.records

    async def test_an_attachment_list_read_in_part_removes_nothing(self, api, db, checkpoints, search) -> None:
        self._page_with(api, search, "att1", "att2")
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        self._page_with(api, search, "att1", version=2)
        api.on("GET", f"{V2}/pages/10/attachments", [
            {"results": [{"id": "att1", "title": "att1.pdf", "mediaType": "application/pdf", "fileSize": 10,
                          "version": {"number": 1}}],
             "_links": {"base": WIKI, "next": f"{V2}/pages/10/attachments?cursor=A2"}},
            json_response({"message": "busy"}, status=503),
        ])
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert {"att1", "att2"} <= set(db.records)


class TestAuditLogKeepsStubsAsStubs:
    async def test_a_restriction_change_on_a_stub_does_not_make_it_indexable(self, api, db, checkpoints, search) -> None:
        stub = WebpageRecord(
            org_id="org-1", record_name="Out of scope", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="10",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, is_placeholder=True,
        )
        db.records["10"] = stub
        search.by_cursor[None] = search_page([v1_page("10")])
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content_permissions_by_titles(["Page 10"])

        assert db.records["10"].is_placeholder is True
        assert db.records["10"].access_rule == AccessRule.RESTRICTED, "its access is still updated"


class TestFullListingMisses:
    """A full listing that misses a stored current page reads it again by id, so a full sync keeps it."""

    async def test_a_page_the_full_listing_missed_is_read_again_by_id(self, api, db, checkpoints, search) -> None:
        helper = TestContentGoneFromSource()
        connector = await helper._two_pages_synced(api, db, checkpoints, search)
        helper._refilter(connector, checkpoints, search)
        search.indexed = {"11": v1_page("11", version=2, attachments=[SPEC])}
        writes_before = len(db.record_batches)

        await connector._sync_spaces_content([ENG])

        rewritten = {r.external_record_id for batch in db.record_batches[writes_before:] for r in batch}
        assert {"10", "11"} <= rewritten, "the missed page is written again, so the full sync sees it"
        assert connector.full_sync_coverage().complete is True

    async def test_a_missed_page_that_cannot_be_read_again_is_kept_and_reported(
        self, api, db, checkpoints, search
    ) -> None:
        helper = TestContentGoneFromSource()
        connector = await helper._two_pages_synced(api, db, checkpoints, search)
        helper._refilter(connector, checkpoints, search)
        search.indexed = {}

        await connector._sync_spaces_content([ENG])

        assert "11" in db.records
        assert connector.full_sync_coverage().complete is False, "the full sync must not unlink it"
        assert any("could not be read in this sync" in p for p in connector._sync_problems)


class TestNoRawErrorTextForUsers:
    async def test_an_unexpected_setup_error_does_not_show_its_text(self, api, db, checkpoints, monkeypatch) -> None:
        connector, _ = await make_connector(db, checkpoints)

        async def broken(*_: object, **__: object) -> None:
            raise RuntimeError("connect to http://10.0.0.5:8090 failed")

        monkeypatch.setattr(connector, "_build_confluence_client", broken)

        with pytest.raises(ConnectorInitError) as err:
            await connector.init()

        assert "10.0.0.5" not in str(err.value)


class TestSpentQuotaEdges:
    async def test_a_response_the_policy_refuses_to_wait_for_is_closed(self, monkeypatch) -> None:
        from app.sources.client.http.http_resilient_transport import (
            ResilientHTTPTransport,
        )

        closed: list[bool] = []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):  # noqa: ANN204
                yield b""

            async def aclose(self) -> None:
                closed.append(True)

        async def answer_429(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "1800"}, stream=Body(), request=request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", answer_429)
        transport = ResilientHTTPTransport(QuotaAwarePolicy(rate_limit=100, max_retries=3))

        with pytest.raises(AtlassianQuotaExhausted):
            await transport.handle_async_request(httpx.Request("GET", f"{SITE}/wiki/api/v2/spaces"))

        assert closed == [True], "the pooled connection is given back"

    async def test_opening_a_page_while_the_quota_is_spent_says_when_to_retry(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        page = WebpageRecord(
            org_id="org-1", record_name="Page", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="10",
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
            version=0, mime_type=MimeTypes.HTML.value,
        )

        async def spent(*_: object, **__: object) -> str:
            raise AtlassianQuotaExhausted(time.time() + 600)

        connector._fetch_page_content = spent

        with pytest.raises(HTTPException) as err:
            await connector.stream_record(page)

        assert err.value.status_code == 429
        assert int(err.value.headers["Retry-After"]) > 0


class TestCommentRecordsFromOlderVersions:
    """Older versions stored each comment as its own record; a page stored as blocks carries them."""

    async def _html_page_with_comment_records(self, api, db, checkpoints, search) -> ConfluenceConnector:
        search.by_cursor[None] = search_page([v1_page("10")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        db.records["10"] = db.records["10"].model_copy(update={"mime_type": MimeTypes.HTML.value})
        comment = CommentRecord(
            org_id="org-1", record_name="c", record_type=RecordType.COMMENT, external_record_id="c1",
            external_record_group_id="77", parent_external_record_id="10", parent_record_type=RecordType.WEBPAGE,
            connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
            version=0, author_source_id="acc-ana",
        )
        db.records["c1"] = comment
        db.records["c2"] = comment.model_copy(update={"id": "reply-id", "external_record_id": "c2",
                                                      "parent_external_record_id": "c1"})
        return connector

    async def test_they_go_once_the_page_is_stored_as_blocks(self, api, db, checkpoints, search) -> None:
        connector = await self._html_page_with_comment_records(api, db, checkpoints, search)
        search.by_cursor[None] = search_page([v1_page("10", version=2)])

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].mime_type == MimeTypes.BLOCKS.value
        assert not {"c1", "c2"} & set(db.records)

    async def test_they_stay_when_comments_are_not_indexed(self, api, db, checkpoints, search, monkeypatch) -> None:
        connector = await self._html_page_with_comment_records(api, db, checkpoints, search)

        async def off(_: object) -> bool:
            return False

        monkeypatch.setattr(connector, "_comments_indexed", off)
        search.by_cursor[None] = search_page([v1_page("10", version=2)])

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].mime_type == MimeTypes.BLOCKS.value
        assert {"c1", "c2"} <= set(db.records), "the blocks carry no comments, so the records are all there is"

    async def test_a_reindex_drops_them_from_a_page_already_stored_as_blocks(
        self, api, db, checkpoints, search
    ) -> None:
        # Stored as blocks while comments were not indexed; comment indexing is on again.
        connector = await self._html_page_with_comment_records(api, db, checkpoints, search)
        db.records["10"] = db.records["10"].model_copy(update={"mime_type": MimeTypes.BLOCKS.value})
        api.on("GET", f"{V2}/pages/10", {"id": "10", "title": "Page 10", "spaceId": "77", "version": {"number": 1}})

        await connector.reindex_records([db.records["10"]])

        assert not {"c1", "c2"} & set(db.records)

    async def test_an_unchanged_html_page_keeps_them(self, api, db, checkpoints, search) -> None:
        connector = await self._html_page_with_comment_records(api, db, checkpoints, search)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].mime_type == MimeTypes.HTML.value
        assert {"c1", "c2"} <= set(db.records), "an HTML page has no comments of its own"


class TestSmallAuditFixes:
    async def test_a_site_url_typed_with_wiki_is_not_doubled(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints, {"auth": {
            "authType": "API_TOKEN", "baseUrl": f"{SITE}/wiki/", "email": "svc@acme.com", "apiToken": "tok"}, "filters": {}})

        assert connector.external_client.get_client().get_base_url() == f"{SITE}/wiki/api/v2"

    async def test_cleanup_closes_the_http_client(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        api.on("GET", f"{V2}/spaces", {"results": []})
        await connector.test_connection_and_access()
        client = connector.external_client.get_client()
        assert client.client is not None

        await connector.cleanup()

        assert client.client is None

    async def test_cleanup_during_a_reindex_closes_the_client_after_it(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        api.on("GET", f"{V2}/spaces", {"results": []})
        await connector.test_connection_and_access()
        client = connector.external_client.get_client()
        open_during: list[bool] = []

        async def reindex(_: object) -> None:
            await connector.cleanup()  # the instance is evicted meanwhile
            open_during.append(client.client is not None)

        connector._reindex_records = reindex
        await connector.reindex_records([])

        assert open_during == [True] and client.client is None

    async def test_a_space_that_fails_does_not_stop_the_others(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        done: list[str] = []

        async def one(space: object) -> None:
            if space.short_name == "ENG":
                raise RuntimeError("boom")
            done.append(space.short_name)

        connector._sync_space_content = one
        await connector._sync_spaces_content([ENG, ENG.model_copy(update={"short_name": "OPS"})])

        assert done == ["OPS"]
        assert any("Space ENG: sync failed" in p for p in connector._sync_problems)

    async def test_a_cancelled_sync_sends_no_notice(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)
        for step in ("_sync_users", "register_authenticated_source_user"):
            setattr(connector, step, AsyncMock())
        connector._sync_user_groups = AsyncMock(side_effect=lambda: connector._note_problem("a group could not be read"))
        connector._sync_spaces = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await connector.run_sync()

        assert connector._notification_service.sent == []

    async def test_a_restriction_entry_without_an_id_keeps_the_page_restricted(
        self, api, db, checkpoints, search
    ) -> None:
        search.by_cursor[None] = search_page([v1_page("10")])
        api.on("GET", f"{V1}/content/10/restriction", {"results": [{"operation": "read", "restrictions": {
            "user": {"results": [{"displayName": "Someone"}]}, "group": {"results": []}}}]})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].access_rule == AccessRule.RESTRICTED, "not opened to the whole space"


class TestSpaceGrantErrors:
    async def test_a_failed_user_lookup_keeps_the_spaces_stored_access(self, api, db, checkpoints) -> None:
        api.on("GET", f"{V2}/spaces/1/permissions", {"results": [
            {"principal": {"type": "user", "id": "acc-ana"}, "operation": {"key": "read", "targetType": "space"}},
        ]})
        connector, _ = await ready_connector(db, checkpoints)

        async def broken(**_: object) -> None:
            raise RuntimeError("graph store unavailable")

        db.get_user_by_source_id = broken

        assert await connector._fetch_space_permissions("1", "Engineering") is None, "not saved without the grant"


class TestFoldersGoneFromConfluence:
    async def _folder_synced(self, api, db, checkpoints, search) -> ConfluenceConnector:
        folder = {**v1_page("500"), "type": "folder", "title": "Specs"}
        search.folders = [folder]
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_folders("ENG")
        assert db.records["500"].external_record_group_id == "77"
        search.folders = []
        return connector

    async def test_a_deleted_folder_is_removed(self, api, db, checkpoints, search) -> None:
        connector = await self._folder_synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/folders/500", json_response({"message": "not found"}, status=404))

        await connector._remove_folders_gone(ENG)

        assert "500" not in db.records

    async def test_a_folder_the_search_misses_but_confluence_still_has_is_kept(self, api, db, checkpoints, search) -> None:
        connector = await self._folder_synced(api, db, checkpoints, search)
        api.on("GET", f"{V2}/folders/500", {"id": "500", "status": "current", "spaceId": "77", "title": "Specs"})

        await connector._remove_folders_gone(ENG)

        assert "500" in db.records

    async def test_a_failed_folder_search_removes_nothing(self, api, db, checkpoints, search) -> None:
        connector = await self._folder_synced(api, db, checkpoints, search)
        api.on("GET", f"{V1}/content/search", json_response({"message": "busy"}, status=503))
        api.on("GET", f"{V2}/folders/500", json_response({"message": "not found"}, status=404))

        await connector._remove_folders_gone(ENG)

        assert "500" in db.records


class TestWebLinksThroughTheGateway:
    async def test_a_page_link_points_at_the_site_not_the_api_gateway(self, api, db, checkpoints, search) -> None:
        page = v1_page("10")
        page["_links"]["self"] = f"https://api.atlassian.com{V1}/content/10"
        search.by_cursor[None] = search_page([page])
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)

        assert db.records["10"].weburl == f"{WIKI}/spaces/ENG/pages/10"


class TestUserSearchCap:
    async def test_reaching_the_user_search_cap_is_reported(self, api, db, checkpoints, monkeypatch) -> None:
        from app.connectors.sources.atlassian.confluence_cloud import (
            connector as module,
        )

        monkeypatch.setattr(module, "USER_SEARCH_CAP", 150)

        def users(first: int, count: int) -> dict[str, Any]:
            return {"results": [{"user": {"accountId": f"acc-{i}"}, "email": f"u{i}@acme.com"}
                                for i in range(first, first + count)], "_links": {"base": WIKI}}

        api.on("GET", f"{V1}/search/user", [users(0, 100), users(100, 50), {"results": []}])
        connector, _ = await ready_connector(db, checkpoints)
        connector._account_statuses_known = True

        await connector._sync_users()

        assert any("returns at most" in p for p in connector._sync_problems)


def comment_on(page_id: str, when: str) -> dict[str, Any]:
    return {"id": f"c-{page_id}-{when}", "type": "comment", "version": {"when": when},
            "container": {"id": page_id, "type": "page"}}


class TestCommentsReindexTheirPage:
    """A comment changes neither its page's version nor its last modified time."""

    async def _synced(self, api, db, checkpoints, search) -> ConfluenceConnector:
        search.by_cursor[None] = search_page([v1_page("10"), v1_page("11")])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await connector._reindex_pages_with_changed_comments("ENG")  # the first check only starts the clock
        db.reindexed.clear()
        return connector

    def _checked_at(self, checkpoints) -> str:
        return checkpoints.values_for("confluence_comments/ENG")["checked_at"]

    async def test_a_page_whose_comments_changed_is_reindexed_once(self, api, db, checkpoints, search) -> None:
        connector = await self._synced(api, db, checkpoints, search)
        after = (datetime.fromisoformat(self._checked_at(checkpoints).replace("Z", "+00:00"))
                 + timedelta(minutes=5)).isoformat()
        search.comments = [comment_on("10", after), comment_on("11", "2020-01-01T00:00:00+00:00")]

        await connector._reindex_pages_with_changed_comments("ENG")
        assert [r.external_record_id for r in db.reindexed] == ["10"], "only the page with a new comment"

        # A later run in a quiet space doesn't re-index it again: the check's clock moved.
        db.reindexed.clear()
        key = next(k for k in checkpoints.sync_points if k.endswith("confluence_comments/ENG"))
        checkpoints.sync_points[key]["checked_at"] = (
            datetime.fromisoformat(after) + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        await connector._reindex_pages_with_changed_comments("ENG")
        assert db.reindexed == []

    async def test_a_failed_comment_search_keeps_the_clock(self, api, db, checkpoints, search) -> None:
        connector = await self._synced(api, db, checkpoints, search)
        before = self._checked_at(checkpoints)
        search.comments = json_response({"message": "busy"}, status=503)

        await connector._reindex_pages_with_changed_comments("ENG")

        assert self._checked_at(checkpoints) == before
        assert any("changed comments" in p for p in connector._sync_problems)

    async def test_comments_off_means_no_comment_search(self, api, db, checkpoints, search, monkeypatch) -> None:
        connector = await self._synced(api, db, checkpoints, search)

        async def off(_: object) -> bool:
            return False

        monkeypatch.setattr(connector, "_comments_indexed", off)
        search.queries.clear()

        await connector._reindex_pages_with_changed_comments("ENG")

        assert not any(q["cql"].startswith("type=comment") for q in search.queries)

    async def test_a_page_left_out_of_indexing_stays_out(self, api, db, checkpoints, search) -> None:
        connector = await self._synced(api, db, checkpoints, search)
        db.records["10"].indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        after = (datetime.fromisoformat(self._checked_at(checkpoints).replace("Z", "+00:00"))
                 + timedelta(minutes=5)).isoformat()
        search.comments = [comment_on("10", after)]

        await connector._reindex_pages_with_changed_comments("ENG")

        assert db.reindexed == []

class TestFooterCommentThreads:
    async def test_a_reply_to_a_reply_stays_in_its_thread(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)

        def comment(cid: str, parent: str | None, created: str) -> dict[str, Any]:
            return {"id": cid, "parentCommentId": parent, "version": {"createdAt": created}}

        threads = connector._organize_confluence_comments_to_threads([
            comment("c3", "c2", "2024-05-03T00:00:00.000Z"),
            comment("c1", None, "2024-05-01T00:00:00.000Z"),
            comment("c2", "c1", "2024-05-02T00:00:00.000Z"),
            comment("d1", None, "2024-04-01T00:00:00.000Z"),
        ])

        assert [[c["id"] for c in t] for t in threads] == [["d1"], ["c1", "c2", "c3"]]


class TestRestrictionsWithoutTheAuditLog:
    """Only administrators can read the audit log; without it, restrictions are re-checked from the listing."""

    def _with_read_restriction(self, page: dict[str, Any], *account_ids: str) -> dict[str, Any]:
        users = [{"accountId": a} for a in account_ids]
        return {**page, "restrictions": {"read": {"restrictions": {
            "user": {"results": users, "size": len(users)}, "group": {"results": [], "size": 0}}}}}

    async def test_a_restriction_added_without_an_edit_is_applied(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        search.by_cursor[None] = search_page([self._with_read_restriction(v1_page("10"))])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        assert db.records["10"].access_rule == AccessRule.STRICT
        await connector._open_audit_window()
        api.on("GET", f"{V1}/audit", json_response({"message": "Forbidden"}, status=403))

        # Restricted to Ana, page not edited: the listing shows it, the audit log can't.
        restricted = self._with_read_restriction(v1_page("10"), "acc-ana")
        search.by_cursor = {None: search_page([restricted])}
        search.indexed = {"10": restricted}
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        await connector._sync_permission_changes_from_audit_log([ENG])

        assert db.records["10"].access_rule == AccessRule.RESTRICTED
        assert [p.email for p in db.record_permissions["10"]] == ["ana@acme.com"]
        assert any("audit log (HTTP 403)" in p for p in connector._sync_problems)

    async def test_a_restriction_removed_without_an_edit_opens_the_page_again(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        restricted = self._with_read_restriction(v1_page("10"), "acc-ana")
        search.by_cursor[None] = search_page([self._with_read_restriction(v1_page("10"))])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_content("ENG", RecordType.CONFLUENCE_PAGE)
        await self._refused_audit_log(api, connector)
        search.by_cursor = {None: search_page([restricted])}
        search.indexed = {"10": restricted}
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to("acc-ana"))
        await connector._sync_permission_changes_from_audit_log([ENG])
        assert db.records["10"].access_rule == AccessRule.RESTRICTED

        # The restriction is lifted, page not edited: the listing shows no restriction any more.
        unrestricted = self._with_read_restriction(v1_page("10"))
        search.by_cursor = {None: search_page([unrestricted])}
        search.indexed = {"10": unrestricted}
        api.on("GET", f"{V1}/content/10/restriction", read_restricted_to())
        await connector._sync_permission_changes_from_audit_log([ENG])

        assert db.records["10"].access_rule == AccessRule.STRICT
        assert db.record_permissions["10"] == [] or all(p.email != "ana@acme.com" for p in db.record_permissions["10"])

    async def _refused_audit_log(self, api, connector) -> None:
        await connector._open_audit_window()
        api.on("GET", f"{V1}/audit", json_response({"message": "Forbidden"}, status=403))

    async def test_a_folder_restricted_without_an_edit_is_applied(self, api, db, checkpoints, search) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        folder = {**v1_page("500"), "type": "folder", "title": "Specs"}
        search.folders, search.blogposts = [self._with_read_restriction(folder)], []
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_folders("ENG")
        assert db.records["500"].access_rule == AccessRule.STRICT
        await self._refused_audit_log(api, connector)

        search.folders = [self._with_read_restriction(folder, "acc-ana")]
        api.on("GET", f"{V1}/content/500/restriction", read_restricted_to("acc-ana"))
        await connector._sync_permission_changes_from_audit_log([ENG])

        assert db.records["500"].access_rule == AccessRule.RESTRICTED
        assert [p.email for p in db.record_permissions["500"]] == ["ana@acme.com"]

    async def test_a_stub_restricted_without_an_edit_gets_the_restriction_and_stays_a_stub(
        self, api, db, checkpoints, search
    ) -> None:
        db.add_user("acc-ana", "ana@acme.com")
        db.records["5"] = WebpageRecord(
            org_id="org-1", record_name="Out of scope", record_type=RecordType.CONFLUENCE_PAGE, external_record_id="5",
            external_record_group_id="77", connector_name=Connectors.CONFLUENCE, connector_id=CONNECTOR_ID,
            origin=OriginTypes.CONNECTOR, version=0, is_placeholder=True,
        )
        search.folders, search.blogposts = [], []
        restricted = self._with_read_restriction(v1_page("5"), "acc-ana")
        search.by_cursor = {None: search_page([restricted])}
        search.indexed = {"5": restricted}
        api.on("GET", f"{V1}/content/5/restriction", read_restricted_to("acc-ana"))
        connector, _ = await ready_connector(db, checkpoints)
        await self._refused_audit_log(api, connector)

        await connector._sync_permission_changes_from_audit_log([ENG])

        assert db.records["5"].is_placeholder is True
        assert db.records["5"].access_rule == AccessRule.RESTRICTED
        assert [p.email for p in db.record_permissions["5"]] == ["ana@acme.com"]


class TestFoldersAboveInScopePages:
    async def test_a_full_sync_keeps_a_folder_the_filters_leave_out_while_pages_hang_under_it(
        self, api, db, checkpoints, search
    ) -> None:
        folder = {**v1_page("500"), "type": "folder", "title": "Specs"}
        search.folders = [folder]
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_folders("ENG")
        db.records["10"] = db.records["500"].model_copy(
            update={"id": "page-10", "external_record_id": "10", "parent_external_record_id": "500"}
        )
        db.full_sync = True

        await connector._remove_folders_gone(ENG)

        assert db.records["500"].id in db.kept_records

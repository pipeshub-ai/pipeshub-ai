"""Confluence Data Center (team) sync, driven end to end over a fake REST API.

The connector, its Confluence client and its request builder are all real; the
Confluence server is an in-memory stub and our databases are in-memory fakes.
The focus is on who ends up able to see what: users, groups, space grants,
page restrictions, and the audit-log pass that catches restriction changes.
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

from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)
from app.models.entities import RecordType
from app.models.permission import EntityType, PermissionType
from app.sources.client.confluence.confluence import ConfluenceRESTClientViaToken
from app.sources.external.confluence.confluence import ConfluenceDataSource

CONNECTOR_ID = "conf-dc-team-1"
BASE = "https://confluence.example.com"
API = "/rest/api"
FAKE_PAT = "fake-pat-for-tests"
AUDIT = "/rest/auditing/1.0/events"


class TeamDb(FakeRecordsDb):
    """Adds the user and group lookups the team connector makes."""

    def __init__(self) -> None:
        super().__init__()
        self.migrations: list[dict[str, Any]] = []

    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> object:
        return next((u for u in reversed(self.app_users) if u.source_user_id == source_user_id), None)

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str) -> object:
        return next((g for g, _ in reversed(self.user_groups) if g.source_user_group_id == external_id), None)

    async def get_all_app_users(self, connector_id: str) -> list[Any]:
        return list(self.app_users)

    async def migrate_group_to_user_by_external_id(self, **kwargs: str) -> None:
        self.migrations.append(kwargs)

    def members_of(self, group_name: str) -> Optional[list[str]]:
        for group, members in reversed(self.user_groups):
            if group.name == group_name:
                return sorted(m.email for m in members)
        return None


class TeamStore(FakeCheckpointStore):
    def __init__(self, db: TeamDb) -> None:
        super().__init__()
        self.db = db

    async def get_user_group_by_name(self, connector_id: str, group_name: str) -> object:
        return next((g for g, _ in reversed(self.db.user_groups) if g.name == group_name), None)


@pytest.fixture
def db() -> TeamDb:
    return TeamDb()


@pytest.fixture
def store(db: TeamDb) -> TeamStore:
    return TeamStore(db)


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
    version: int = 1,
    attachments: Optional[list[dict[str, Any]]] = None,
    ctype: str = "page",
    space_id: int = 10,
) -> dict[str, Any]:
    atts = attachments or []
    return {
        "id": cid,
        "type": ctype,
        "title": f"Title {cid}",
        "space": {"id": space_id, "key": "ENG"},
        "version": {"number": version, "when": "2024-05-01T10:00:00.000Z"},
        "history": {
            "createdDate": "2024-01-01T00:00:00.000Z",
            "lastUpdated": {"when": "2024-05-01T10:00:00.000Z", "number": version},
        },
        "ancestors": [],
        "children": {"attachment": {"results": atts, "size": len(atts)}},
        "_links": {"webui": f"/pages/viewpage.action?pageId={cid}"},
    }


def attachment(aid: str, version: int = 1, container: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": aid,
        "title": f"{aid}.pdf",
        "version": {"number": version, "when": "2024-05-01T10:00:00.000Z"},
        "extensions": {"mediaType": "application/pdf", "fileSize": 10},
        "_links": {"webui": f"/download/attachments/{aid}", "download": f"/download/attachments/p1/{aid}.pdf?version={version}"},
    }
    if container:
        data["container"] = container
    return data


def comment(cid: str, version: int = 1, **extra: object) -> dict[str, Any]:
    return {
        "id": cid,
        "title": f"Re: {cid}",
        "version": {"number": version, "when": "2024-05-02T10:00:00.000Z", "by": {"userKey": "u-alice"}},
        "extensions": {"location": "footer"},
        "_links": {"webui": f"/comment/{cid}"},
        **extra,
    }


def listing(results: list[dict[str, Any]], next_start: Optional[int] = None) -> dict[str, Any]:
    links: dict[str, Any] = {"base": BASE}
    if next_start is not None:
        links["next"] = f"{API}/content/search?limit=50&start={next_start}"
    return {"results": results, "_links": links}


def restricted_to(users: list[dict[str, Any]] = (), groups: list[dict[str, Any]] = ()) -> dict[str, Any]:
    return {
        "viewContentRestrictions": {
            "results": [
                {"operation": "read", "restrictions": {"user": {"results": list(users)}, "group": {"results": list(groups)}}}
            ]
        }
    }


def user(key: str, email: Optional[str]) -> dict[str, Any]:
    data = {"userKey": key, "username": key, "displayName": key.title()}
    if email:
        data["email"] = email
    return data


class ContentSearch:
    """Answers ``/content/search`` per (type, start) and remembers each CQL."""

    def __init__(self) -> None:
        self.pages: dict[tuple[str, int], Any] = {}
        self.cql: list[str] = []

    def add(self, ctype: str, start: int, response: object) -> None:
        self.pages[(ctype, start)] = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        cql = q["cql"]
        self.cql.append(cql)
        ctype = "page" if cql.startswith("type=page") else "blogpost"
        response = self.pages.get((ctype, int(q.get("start", 0))), listing([]))
        return response if isinstance(response, httpx.Response) else json_response(response)


@pytest.fixture
def search(atlassian_api: AtlassianApiStub) -> ContentSearch:
    handler = ContentSearch()
    atlassian_api.on("GET", f"{API}/content/search", handler)
    return handler


def with_directory(api: AtlassianApiStub, users: list[dict[str, Any]], groups: dict[str, list[dict[str, Any]]]) -> None:
    api.on("GET", f"{API}/user/list", {"results": users})
    api.on("GET", f"{API}/group", {"results": [{"type": "group", "name": name} for name in groups]})
    for name, members in groups.items():
        api.on("GET", f"{API}/group/{name}/member", {"results": members})


async def make_connector(
    api: AtlassianApiStub, db: TeamDb, store: TeamStore, filters: Optional[dict[str, Any]] = None
) -> ConfluenceDataCenterConnector:
    config = {
        "auth": {"authType": "API_TOKEN", "baseUrl": f"{BASE}/", "apiToken": FAKE_PAT},
        "filters": filters or {},
    }
    connector = ConfluenceDataCenterConnector(
        logging.getLogger("test.confluence_dc_team"),
        db,
        store,
        FakeConfigService(CONNECTOR_ID, config),
        CONNECTOR_ID,
        "team",
        "admin-user-1",
    )
    assert await connector.init() is True
    for suffix, body in (
        ("/child/comment", {"results": [], "_links": {"base": BASE}}),
        ("/child/attachment", {"results": [], "_links": {"base": BASE}}),
        ("/restriction/relevantViewRestrictions", {"viewContentRestrictions": {"results": []}}),
        ("/permissions", json_response([])),
    ):
        api.on_suffix("GET", suffix, body)
    api.on("GET", f"{API}/server-information", {"version": "9.2.0", "versionNumbers": [9, 2, 0]})
    api.on("GET", f"{API}/user/list", {"results": []})
    api.on("GET", f"{API}/group", {"results": []})
    api.on("GET", f"{API}/space", {"results": [space("ENG", 10)], "_links": {"base": BASE}})
    api.on("GET", AUDIT, {"entities": [], "pagingInfo": {"lastPage": True}})
    api.install(connector.external_client.get_client())
    return connector


def audit_key(store: TeamStore) -> Optional[dict[str, Any]]:
    return store.values_for("permissions/audit_log")


class TestTheStubIsTheRealClientStack:
    async def test_connector_talks_through_real_client_with_bearer_token(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        assert type(connector.data_source) is ConfluenceDataSource
        assert type(connector.external_client.get_client()) is ConfluenceRESTClientViaToken

        assert await connector.test_connection_and_access() is True
        request = atlassian_api.requests[-1]
        assert request.url.path == f"{API}/space"
        assert request.headers["Authorization"] == f"Bearer {FAKE_PAT}"

    async def test_sync_refuses_to_run_before_init(self, db, store) -> None:
        connector = ConfluenceDataCenterConnector(
            logging.getLogger("t"), db, store, FakeConfigService(CONNECTOR_ID, {}), CONNECTOR_ID, "team", "u"
        )
        assert await connector.init() is False
        with pytest.raises(Exception, match="not initialized"):
            await connector.run_sync()


class TestUsersAndGroups:
    async def test_every_page_of_users_is_read_and_users_without_email_are_skipped(self, atlassian_api, db, store) -> None:
        first = [user(f"u{i}", f"u{i}@example.com") for i in range(199)] + [user("no-mail", None)]
        pages = {"0": {"results": first}, "200": {"results": [user("last", "last@example.com")]}}
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on(
            "GET", f"{API}/user/list", lambda r: json_response(pages[AtlassianApiStub.query(r).get("start", "0")])
        )

        await connector._sync_users()

        emails = {u.email for u in db.app_users}
        assert len(emails) == 200 and "last@example.com" in emails
        assert all(u.source_user_id != "no-mail" for u in db.app_users)
        assert [AtlassianApiStub.query(r)["start"] for r in atlassian_api.calls("GET", f"{API}/user/list")] == ["0", "200"]
        assert {m["group_external_id"] for m in db.migrations} >= {"u0", "last"}, (
            "access held by a stand-in group for an email-less user moves to the real user once their email is known"
        )

    async def test_group_members_are_read_to_the_end_and_missing_emails_are_looked_up(self, atlassian_api, db, store) -> None:
        people = [user(f"m{i}", f"m{i}@example.com") for i in range(200)] + [user("ldap", "ldap@example.com")]
        connector = await make_connector(atlassian_api, db, store)
        user_pages = {"0": people[:200], "200": people[200:]}
        atlassian_api.on(
            "GET", f"{API}/user/list",
            lambda r: json_response({"results": user_pages[AtlassianApiStub.query(r).get("start", "0")]}),
        )
        atlassian_api.on("GET", f"{API}/group", {"results": [{"type": "group", "name": "eng team"}]})
        member_pages = {"0": people[:200], "200": [user("ldap", None)]}
        atlassian_api.on(
            "GET",
            f"{API}/group/eng team/member",
            lambda r: json_response({"results": member_pages[AtlassianApiStub.query(r).get("start", "0")]}),
        )
        atlassian_api.on("GET", f"{API}/user", lambda r: json_response(user(AtlassianApiStub.query(r)["key"], "ldap@example.com")))

        await connector._sync_users()
        await connector._sync_user_groups()

        members = db.members_of("eng team")
        assert members is not None and len(members) == 201 and "ldap@example.com" in members
        member_calls = [r for r in atlassian_api.requests if r.url.path == f"{API}/group/eng team/member"]
        assert b"/group/eng%20team/member" in member_calls[0].url.raw_path
        (lookup,) = atlassian_api.calls("GET", f"{API}/user")
        assert AtlassianApiStub.query(lookup) == {"key": "ldap"}

    async def test_groups_are_read_to_the_end(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        pages = {"0": [{"name": f"g{i}"} for i in range(50)], "50": [{"name": "g50"}]}
        atlassian_api.on("GET", f"{API}/group", lambda r: json_response({"results": pages[AtlassianApiStub.query(r).get("start", "0")]}))

        await connector._sync_user_groups()

        assert {g.name for g, _ in db.user_groups} == {f"g{i}" for i in range(51)}

    async def test_a_failed_member_listing_does_not_empty_the_group(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {"eng": [user("alice", "alice@example.com")]})
        await connector._sync_users()
        await connector._sync_user_groups()
        assert db.members_of("eng") == ["alice@example.com"]

        atlassian_api.on("GET", f"{API}/group/eng/member", json_response({"message": "busy"}, status=503))
        await connector._sync_user_groups()

        assert db.members_of("eng") == ["alice@example.com"]


class TestSpacePermissions:
    async def test_space_grants_map_to_known_users_and_groups(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {"eng": []})
        atlassian_api.on(
            "GET",
            f"{API}/space/ENG/permissions",
            json_response(
                [
                    {"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "user", "userKey": "alice"}},
                    {"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "group", "name": "eng"}},
                    {"operation": {"operationKey": "administer", "targetType": "space"}, "subject": {"type": "user", "userKey": "alice"}},
                    {"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "anonymous"}},
                    {"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "user", "userKey": "stranger"}},
                    {"operation": {"operation": "create", "targetType": "page"}, "subjects": {"group": {"results": [{"name": "eng"}]}}},
                ]
            ),
        )

        await connector.run_sync()

        grants = {(p.entity_type, p.email or p.external_id, p.type) for p in db.record_group_permissions["10"]}
        assert grants == {
            (EntityType.USER, "alice@example.com", PermissionType.READ),
            (EntityType.GROUP, "eng", PermissionType.READ),
            (EntityType.USER, "alice@example.com", PermissionType.OWNER),
            (EntityType.GROUP, "eng", PermissionType.WRITE),
        }, "anonymous and unknown users grant nothing"

    async def test_group_known_only_by_name_is_still_granted(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/group", {"results": [{"type": "group", "id": "g-uuid-1", "name": "writers"}]})
        atlassian_api.on("GET", f"{API}/group/writers/member", {"results": []})
        atlassian_api.on(
            "GET",
            f"{API}/space/ENG/permissions",
            json_response([{"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "group", "name": "writers"}}]),
        )

        await connector.run_sync()

        (grant,) = db.record_group_permissions["10"]
        assert (grant.entity_type, grant.external_id) == (EntityType.GROUP, "g-uuid-1")

    async def test_old_server_without_permissions_api_is_not_asked(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/server-information", {"version": "8.5.4", "versionNumbers": [8, 5, 4]})

        await connector.run_sync()

        assert "10" in db.record_groups, "the space is still synced"
        assert atlassian_api.calls("GET", f"{API}/space/ENG/permissions") == []

    async def test_unknown_server_version_is_treated_as_modern(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/server-information", json_response({}, status=500))

        await connector.run_sync()

        assert len(atlassian_api.calls("GET", f"{API}/space/ENG/permissions")) == 1
        assert len(atlassian_api.calls("GET", f"{API}/server-information")) == 1, "probed once, then cached"


class TestPageRestrictions:
    async def test_restricted_page_is_limited_to_the_named_people(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1"), content("open")]))
        atlassian_api.on(
            "GET",
            f"{API}/content/p1/restriction/relevantViewRestrictions",
            restricted_to(users=[{"type": "known", "userKey": "alice"}, {"type": "known", "userKey": "contractor"}]),
        )

        await connector.run_sync()

        page = db.records["p1"]
        assert page.inherit_permissions is False, "space members must not see a restricted page"
        grants = {(p.entity_type, p.email or p.external_id) for p in db.record_permissions["p1"]}
        assert grants == {(EntityType.USER, "alice@example.com"), (EntityType.GROUP, "contractor")}
        stand_in = [g for g, _ in db.user_groups if g.source_user_group_id == "contractor"]
        assert stand_in, "a user with no email yet keeps access through a stand-in group"
        assert db.records["open"].inherit_permissions is True
        assert db.record_permissions["open"] == []

    async def test_a_failed_restriction_lookup_does_not_open_up_a_restricted_page(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1")]))
        restriction = f"{API}/content/p1/restriction/relevantViewRestrictions"
        atlassian_api.on("GET", restriction, restricted_to(users=[{"userKey": "alice"}]))
        await connector.run_sync()
        assert db.records["p1"].inherit_permissions is False

        atlassian_api.on("GET", restriction, json_response({"message": "rate limited"}, status=429))
        await connector.run_sync()

        assert db.records["p1"].inherit_permissions is False

    async def test_a_new_page_with_unreadable_restrictions_waits_for_the_next_sync(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")]), content("open")]))
        atlassian_api.on("GET", f"{API}/content/p1/restriction/relevantViewRestrictions", json_response({"message": "busy"}, status=503))

        await connector.run_sync()

        assert "p1" not in db.records and "att1" not in db.records
        assert "open" in db.records
        assert store.values_for("confluence_pages/ENG") is None, "the window is read again next time"

    async def test_reindex_with_unreadable_restrictions_keeps_the_stored_access(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1")]))
        restriction = f"{API}/content/p1/restriction/relevantViewRestrictions"
        atlassian_api.on("GET", restriction, restricted_to(users=[{"userKey": "alice"}]))
        await connector.run_sync()
        atlassian_api.on("GET", f"{API}/content/p1", content("p1", version=2))
        atlassian_api.on("GET", restriction, json_response({"message": "busy"}, status=503))

        await connector.reindex_records([db.records["p1"]])

        assert db.content_updates == [] and db.permission_updates == []
        assert [r.external_record_id for r in db.reindexed] == ["p1"]

    async def test_files_and_comments_of_a_restricted_page_stay_restricted(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")])]))
        atlassian_api.on("GET", f"{API}/content/p1/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})

        await connector.run_sync()

        assert db.records["p1"].inherit_permissions is False
        assert db.records["att1"].inherit_permissions is False
        assert db.records["c1"].inherit_permissions is False

    async def test_page_restricted_to_a_group_named_only_by_name_stays_restricted(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [], {"finance": []})
        search.add("page", 0, listing([content("p1")]))
        atlassian_api.on(
            "GET",
            f"{API}/content/p1/restriction/relevantViewRestrictions",
            restricted_to(groups=[{"type": "group", "name": "finance"}]),
        )

        await connector.run_sync()

        assert db.records["p1"].inherit_permissions is False
        assert [(p.entity_type, p.external_id) for p in db.record_permissions["p1"]] == [(EntityType.GROUP, "finance")]


class TestContentSync:
    async def test_all_listing_pages_are_synced_then_only_recent_changes_are_asked_for(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1"), content("p2")], next_start=2))
        search.add("page", 2, listing([content("p3")]))
        search.add("blogpost", 0, listing([content("b1", ctype="blogpost")]))

        await connector.run_sync()

        assert {k for k, r in db.records.items() if r.record_type == RecordType.CONFLUENCE_PAGE} == {"p1", "p2", "p3"}
        assert db.records["b1"].record_type == RecordType.CONFLUENCE_BLOGPOST
        checkpoint = store.values_for("confluence_pages/ENG")["last_sync_time"]
        ids_before = {k: r.id for k, r in db.records.items()}

        search.cql.clear()
        search.add("page", 0, listing([content("p1", version=2)]))
        search.add("page", 2, listing([]))
        await connector.run_sync()

        since = datetime.strptime(checkpoint, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc) - timedelta(hours=24)
        assert any(f'lastModified > "{since.strftime("%Y-%m-%d %H:%M")}"' in c for c in search.cql if c.startswith("type=page"))
        assert {k: r.id for k, r in db.records.items()} == ids_before, "updated in place, no duplicates"
        assert db.records["p1"].external_revision_id == "2"

    async def test_a_failed_listing_page_does_not_move_the_checkpoint(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")], next_start=1))
        search.add("page", 1, json_response({"message": "busy"}, status=503))

        await connector.run_sync()

        assert "p1" in db.records
        assert store.values_for("confluence_pages/ENG") is None

    async def test_homepage_missing_from_search_is_backfilled_with_its_restrictions(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})

        def spaces(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("expand") == "homepage":
                return json_response({"results": [{**space("ENG", 10), "homepage": {"id": 500, "title": "Home"}}]})
            return json_response({"results": [space("ENG", 10)], "_links": {"base": BASE}})

        atlassian_api.on("GET", f"{API}/space", spaces)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        atlassian_api.on("GET", f"{API}/content/500/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))
        search.add("page", 0, listing([content("p1")]))

        await connector.run_sync()

        home = db.records["500"]
        assert home.inherit_permissions is False
        assert [p.email for p in db.record_permissions["500"]] == ["alice@example.com"]

    async def test_comment_replies_and_their_files_are_synced_with_the_page_grants(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        page_image = {**attachment("img1"), "title": "diagram.png"}
        search.add("page", 0, listing([content("p1", attachments=[page_image])]))
        atlassian_api.on("GET", f"{API}/content/p1/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        reply = comment("c2", body={"storage": {"value": '<ac:image><ri:attachment ri:filename="Diagram.PNG" /></ac:image>'}})
        atlassian_api.on("GET", f"{API}/content/c1/child/comment", {"results": [reply], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c2/child/attachment", {"results": [attachment("c2file")], "_links": {"base": BASE}})

        await connector.run_sync()

        page = db.records["p1"]
        assert db.records["c2"].parent_external_record_id == "c1"
        assert db.records["c2"].parent_node_id == page.id
        c2file = db.records["c2file"]
        assert (c2file.parent_external_record_id, c2file.parent_record_type) == ("c2", RecordType.COMMENT)
        for rid in ("c1", "c2", "c2file", "img1"):
            assert [p.email for p in db.record_permissions[rid]] == ["alice@example.com"], rid


class TestAuditLogRestrictionChanges:
    async def test_first_sync_only_starts_the_audit_clock(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)

        await connector.run_sync()

        assert atlassian_api.calls("GET", AUDIT) == []
        assert audit_key(store)["last_sync_time_ms"] > 0

    async def test_restriction_changes_from_the_audit_log_are_applied_to_synced_pages_only(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1"), content("p2")]))
        await connector.run_sync()
        assert db.records["p1"].inherit_permissions is True
        first_clock = audit_key(store)["last_sync_time_ms"]
        search.add("page", 0, listing([]))

        def event(obj_id: str, category: str = "Pages and Blogs", kind: str = "Page") -> dict[str, Any]:
            return {
                "type": {"category": category, "action": "Content restriction added"},
                "affectedObjects": [{"name": "x", "type": kind, "id": obj_id}, {"name": "Eng", "type": "Space", "id": "10"}],
            }

        pages = {
            None: {"entities": [event("p1"), event("filtered-out-page")], "pagingInfo": {"lastPage": False, "nextPageCursor": "cur-2"}},
            "cur-2": {"entities": [event("p2", category="Permissions"), event("gone")], "pagingInfo": {"lastPage": True}},
        }
        atlassian_api.on("GET", AUDIT, lambda r: json_response(pages[AtlassianApiStub.query(r).get("pageCursor")]))
        atlassian_api.on("GET", f"{API}/content/p1", {**content("p1"), "type": "page"})
        atlassian_api.on("GET", f"{API}/content/p1/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))
        db.records["gone"] = db.records["p2"].model_copy(update={"external_record_id": "gone"})
        atlassian_api.on("GET", f"{API}/content/gone", json_response({"message": "no"}, status=404))

        await connector.run_sync()

        audit_calls = atlassian_api.calls("GET", AUDIT)
        assert [AtlassianApiStub.query(r).get("pageCursor") for r in audit_calls] == [None, "cur-2"]
        assert AtlassianApiStub.query(audit_calls[0])["categories"] == "Pages and Blogs"
        assert db.records["p1"].inherit_permissions is False
        assert [p.email for p in db.record_permissions["p1"]] == ["alice@example.com"]
        assert "filtered-out-page" not in db.records, "pages excluded by filters are not created by the audit pass"
        assert db.records["p2"].inherit_permissions is True, "space-level 'Permissions' events are not page restrictions"
        assert audit_key(store)["last_sync_time_ms"] > first_clock

    async def test_a_failed_page_lookup_keeps_the_audit_clock_so_the_change_is_retried(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        clock = audit_key(store)["last_sync_time_ms"]
        event = {"type": {"category": "Pages and Blogs"}, "affectedObjects": [{"type": "Page", "id": "p1"}, {"type": "Space", "id": "10"}]}
        atlassian_api.on("GET", AUDIT, {"entities": [event], "pagingInfo": {"lastPage": True}})
        atlassian_api.on("GET", f"{API}/content/p1", json_response({"message": "busy"}, status=503))

        with pytest.raises(ValueError):
            await connector.run_sync()

        assert audit_key(store)["last_sync_time_ms"] == clock

    async def test_an_unreadable_audit_log_does_not_move_the_audit_clock(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        await connector.run_sync()
        clock = audit_key(store)["last_sync_time_ms"]
        atlassian_api.on("GET", AUDIT, json_response({"message": "forbidden"}, status=403))

        await connector.run_sync()

        assert audit_key(store)["last_sync_time_ms"] == clock


class TestReindex:
    async def _synced(self, api, db, store, search) -> ConfluenceDataCenterConnector:
        connector = await make_connector(api, db, store)
        with_directory(api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")])]))
        search.add("blogpost", 0, listing([content("b1", ctype="blogpost")]))
        api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        api.on("GET", f"{API}/content/c1/child/comment", {"results": [comment("c2")], "_links": {"base": BASE}})
        api.on("GET", f"{API}/content/c2/child/attachment", {"results": [attachment("c2file")], "_links": {"base": BASE}})
        await connector.run_sync()
        return connector

    async def test_changed_items_are_refreshed_with_current_restrictions_and_unchanged_ones_just_reindexed(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/content/p1", content("p1", version=2))
        atlassian_api.on("GET", f"{API}/content/p1/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))
        atlassian_api.on("GET", f"{API}/content/b1", content("b1", ctype="blogpost"))
        atlassian_api.on(
            "GET", f"{API}/content/c2",
            comment("c2", version=3, container={"type": "page", "id": "p1"}, _links={"base": BASE, "webui": "/c2"}),
        )
        atlassian_api.on("GET", f"{API}/content/att1", attachment("att1", version=2))
        atlassian_api.on("GET", f"{API}/content/c2file", attachment("c2file", version=4, container={"type": "page", "id": "p1"}))

        await connector.reindex_records([db.records[k] for k in ("p1", "b1", "c2", "att1", "c2file")])

        updated = {r.external_record_id: r for r in db.content_updates}
        assert set(updated) == {"p1", "c2", "att1", "c2file"}
        assert updated["p1"].version == db.records["p1"].version + 1
        assert updated["p1"].inherit_permissions is False
        assert (updated["c2"].parent_external_record_id, updated["c2"].parent_record_type) == ("c1", RecordType.COMMENT)
        assert (updated["c2file"].parent_external_record_id, updated["c2file"].parent_record_type) == ("c2", RecordType.COMMENT)
        restricted = {r.external_record_id for r, perms in db.permission_updates if [p.email for p in perms] == ["alice@example.com"]}
        assert restricted == {"p1", "c2", "att1", "c2file"}
        assert all(updated[k].inherit_permissions is False for k in ("c2", "att1", "c2file")), (
            "a restricted page's files and comments do not pick up the space's access on reindex"
        )
        assert [r.external_record_id for r in db.reindexed] == ["b1"]

    async def test_comment_attachment_resolves_its_page_through_the_comment(self, atlassian_api, db, store, search) -> None:
        connector = await self._synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/content/c2file", attachment("c2file", version=9))
        atlassian_api.on("GET", f"{API}/content/c2", {"id": "c2", "type": "comment", "container": {"type": "page", "id": "p1"}})
        atlassian_api.on("GET", f"{API}/content/p1/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))

        await connector.reindex_records([db.records["c2file"]])

        (updated,) = db.content_updates
        assert updated.parent_node_id == db.records["c2"].id
        ((_, perms),) = db.permission_updates
        assert [p.email for p in perms] == ["alice@example.com"]
        assert atlassian_api.calls("GET", f"{API}/content/p1/restriction/relevantViewRestrictions")

    async def test_reindex_without_init_is_refused(self, db, store) -> None:
        connector = ConfluenceDataCenterConnector(
            logging.getLogger("t"), db, store, FakeConfigService(CONNECTOR_ID, {}), CONNECTOR_ID, "team", "u"
        )
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([object()])


async def _body(response: object) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


class TestStreaming:
    async def _file_record(self, api, db, store, search) -> tuple[ConfluenceDataCenterConnector, Any]:
        connector = await make_connector(api, db, store)
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")])]))
        await connector.run_sync()
        return connector, db.records["att1"]

    async def test_attachment_download_rides_out_rate_limits_and_server_errors(
        self, atlassian_api, db, store, search, backoff_sleeps
    ) -> None:
        connector, record = await self._file_record(atlassian_api, db, store, search)
        atlassian_api.on(
            "GET",
            f"{API}/content/att1",
            [json_response({"message": "slow down"}, status=429, headers={"Retry-After": "7"}), attachment("att1")],
        )
        payload = b"%PDF-1.7 " + b"x" * 20000
        atlassian_api.on(
            "GET",
            "/download/attachments/p1/att1.pdf",
            [json_response({}, status=503), httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})],
        )

        response = await connector.stream_record(record)

        assert await _body(response) == payload
        assert response.media_type == "application/pdf"
        assert backoff_sleeps == [7.0, 0.5], "honours Retry-After, then backs off"

    @pytest.mark.parametrize(
        ("status", "expected", "words"),
        [(401, 409, "Reconnect"), (403, 403, "denied"), (404, 404, "no longer exists")],
    )
    async def test_attachment_errors_say_what_went_wrong(self, atlassian_api, db, store, search, status, expected, words) -> None:
        connector, record = await self._file_record(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/content/att1", json_response({"message": "no"}, status=status))

        response = await connector.stream_record(record)
        with pytest.raises(HTTPException) as err:
            await _body(response)

        assert err.value.status_code == expected
        assert words in err.value.detail

    async def test_forbidden_download_is_not_reported_as_deleted(self, atlassian_api, db, store, search) -> None:
        connector, record = await self._file_record(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/content/att1", attachment("att1"))
        atlassian_api.on("GET", "/download/attachments/p1/att1.pdf", json_response({}, status=403))

        response = await connector.stream_record(record)
        with pytest.raises(HTTPException) as err:
            await _body(response)

        assert err.value.status_code == 403

    async def test_page_html_is_streamed_and_an_expired_token_asks_to_reconnect(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        atlassian_api.on("GET", f"{API}/content/p1", {**content("p1"), "body": {"export_view": {"value": "<p>Quarterly plan</p>"}}})

        response = await connector.stream_record(db.records["p1"])
        assert b"Quarterly plan" in await _body(response)

        atlassian_api.on("GET", f"{API}/content/p1", json_response({"message": "expired"}, status=401))
        with pytest.raises(HTTPException) as err:
            await connector.stream_record(db.records["p1"])
        assert err.value.status_code == 409


def _filters(sync: Optional[dict[str, Any]] = None, indexing: Optional[dict[str, bool]] = None) -> dict[str, Any]:
    return {
        "sync": {"values": sync or {}},
        "indexing": {"values": {k: {"operator": "is", "type": "boolean", "value": v} for k, v in (indexing or {}).items()}},
    }


class TestFiltersAndIndexingSwitches:
    async def test_space_and_blogpost_filters_shape_what_is_fetched(self, atlassian_api, db, store, search) -> None:
        filters = _filters(
            sync={
                "space_keys": {"operator": "not_in", "type": "list", "value": ["HR"]},
                "blogpost_ids": {"operator": "in", "type": "list", "value": ["b1", "b2"]},
                "created": {"operator": "is_before", "type": "datetime", "value": {"end": 1767225600000}},
            },
        )
        connector = await make_connector(atlassian_api, db, store, filters=filters)
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("HR", 30)], "_links": {"base": BASE}})
        search.add("blogpost", 0, listing([content("b1", ctype="blogpost")]))

        await connector.run_sync()

        assert set(db.record_groups) == {"10"}
        assert atlassian_api.calls("GET", f"{API}/space/HR/permissions") == []
        blog_cql = [c for c in search.cql if c.startswith("type=blogpost")]
        assert len(blog_cql) == 1 and "id in (b1, b2)" in blog_cql[0] and "created <" in blog_cql[0]

    async def test_switched_off_pages_and_files_are_kept_but_not_indexed(self, atlassian_api, db, store, search) -> None:
        filters = _filters(indexing={"pages": False, "page_attachments": False})
        connector = await make_connector(atlassian_api, db, store, filters=filters)
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")])]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/attachment", {"results": [attachment("c1file")], "_links": {"base": BASE}})

        await connector.run_sync()

        for rid in ("p1", "att1", "c1file"):
            assert db.records[rid].indexing_status == "AUTO_INDEX_OFF", rid

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: the 'Index Page Comments' "
            "switch is read but never applied, so comments are indexed even when an admin turns "
            "comment indexing off."
        ),
    )
    async def test_switching_off_comment_indexing_is_respected(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store, filters=_filters(indexing={"page_comments": False}))
        search.add("page", 0, listing([content("p1")]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})

        await connector.run_sync()

        assert db.records["c1"].indexing_status == "AUTO_INDEX_OFF"

    async def test_truncated_attachment_list_and_offsetless_next_link_are_followed(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        full = [content(f"p{i}") for i in range(50)]
        full[0] = content("p0", attachments=[attachment("a1")])
        full[0]["children"]["attachment"]["size"] = 2
        search.add("page", 0, {"results": full, "_links": {"base": BASE, "next": f"{API}/content/search?limit=50"}})
        search.add("page", 50, listing([content("p50")]))
        atlassian_api.on("GET", f"{API}/content/p0/child/attachment", {"results": [attachment("a1"), attachment("a2")], "_links": {"base": BASE}})

        await connector.run_sync()

        assert {"p49", "p50", "a1", "a2"} <= set(db.records)
        assert db.records["a2"].parent_node_id == db.records["p0"].id


class TestCommentsDeep:
    async def test_inline_comments_replies_and_pages_of_replies_are_all_kept(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")]))
        inline = comment(
            "i1",
            extensions={
                "location": "inline",
                "inlineProperties": {"originalSelection": "the numbers"},
                "resolution": {"resolved": False},
            },
        )
        atlassian_api.on(
            "GET",
            f"{API}/content/p1/child/comment",
            {"results": [inline, comment("f1")], "_links": {"base": BASE}},
        )
        replies = {"0": {"results": [comment("r1")], "_links": {"base": BASE, "next": f"{API}/content/i1/child/comment?start=1"}},
                   "1": {"results": [comment("r2")], "_links": {"base": BASE}}}
        atlassian_api.on(
            "GET", f"{API}/content/i1/child/comment",
            lambda r: json_response(replies[AtlassianApiStub.query(r).get("start", "0")]),
        )
        atlassian_api.on("GET", f"{API}/content/r1/child/comment", {"results": [comment("r1a")], "_links": {"base": BASE}})

        await connector.run_sync()

        assert db.records["i1"].record_type == RecordType.INLINE_COMMENT
        assert db.records["i1"].resolution_status == "open"
        assert db.records["i1"].comment_selection == "the numbers"
        assert db.records["f1"].record_type == RecordType.COMMENT
        for reply, parent in (("r1", "i1"), ("r2", "i1"), ("r1a", "r1")):
            assert db.records[reply].parent_external_record_id == parent
            assert db.records[reply].record_type == RecordType.INLINE_COMMENT, "replies keep their thread's kind"
        assert all(r.external_record_id != "f1" or r.record_type == RecordType.COMMENT for r in db.records.values())

    async def test_one_broken_comment_does_not_lose_its_siblings(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")]))
        atlassian_api.on(
            "GET", f"{API}/content/p1/child/comment",
            {"results": [comment("c1"), comment("c2"), comment("c3")], "_links": {"base": BASE}},
        )
        db.fail_lookup_for = {"c2"}

        await connector.run_sync()

        assert {"c1", "c3"} <= set(db.records)
        assert "c2" not in db.records


class TestSpaceGrantFailures:
    async def test_a_failed_space_permission_lookup_keeps_existing_access(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        grants = f"{API}/space/ENG/permissions"
        atlassian_api.on(
            "GET", grants,
            json_response([{"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "user", "userKey": "alice"}}]),
        )
        await connector.run_sync()
        assert [p.email for p in db.record_group_permissions["10"]] == ["alice@example.com"]

        atlassian_api.on("GET", grants, json_response({"message": "busy"}, status=503))
        await connector.run_sync()

        assert [p.email for p in db.record_group_permissions["10"]] == ["alice@example.com"]

    async def test_unexpected_permission_payload_grants_nothing(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/space/ENG/permissions", {"unexpected": "shape"})

        await connector.run_sync()

        assert db.record_group_permissions["10"] == []


class TestReindexMore:
    async def test_changed_blogpost_and_comment_found_only_through_ancestors_are_refreshed(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("blogpost", 0, listing([content("b1", ctype="blogpost")]))
        atlassian_api.on("GET", f"{API}/content/b1/child/comment", {"results": [comment("bc1")], "_links": {"base": BASE}})
        await connector.run_sync()

        atlassian_api.on("GET", f"{API}/content/b1", content("b1", version=5, ctype="blogpost"))
        atlassian_api.on(
            "GET", f"{API}/content/bc1",
            comment("bc1", version=2, container={"type": "blogpost", "id": "b1"}, ancestors=[{"type": "blogpost", "id": "b1"}]),
        )
        await connector.reindex_records([db.records["b1"], db.records["bc1"]])

        updated = {r.external_record_id: r for r in db.content_updates}
        assert updated["b1"].external_revision_id == "5"
        assert updated["bc1"].parent_external_record_id == "b1"
        assert updated["bc1"].parent_node_id == db.records["b1"].id

    async def test_items_gone_or_unchanged_at_the_source_are_reindexed_from_what_we_have(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")])]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        await connector.run_sync()
        atlassian_api.on("GET", f"{API}/content/p1", json_response({}, status=404))
        atlassian_api.on("GET", f"{API}/content/c1", comment("c1"))
        atlassian_api.on("GET", f"{API}/content/att1", {**attachment("att1"), "version": {}})

        await connector.reindex_records([db.records[k] for k in ("p1", "c1", "att1")])

        assert db.content_updates == []
        assert {r.external_record_id for r in db.reindexed} == {"p1", "c1", "att1"}


class TestSpaceListingEdges:
    async def test_space_pages_are_followed_and_incomplete_spaces_skipped(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        pages = {
            "0": {"results": [space("ENG", 10), {"id": 11, "key": "NONAME"}], "_links": {"base": BASE, "next": f"{API}/space?start=2"}},
            "2": {"results": [space("OPS", 20)], "_links": {"base": BASE, "next": f"{API}/space?cursor=opaque"}},
        }

        def spaces(request: httpx.Request) -> httpx.Response:
            q = AtlassianApiStub.query(request)
            if q.get("expand") == "homepage":
                return json_response({"results": []})
            return json_response(pages[q.get("start", "0")])

        atlassian_api.on("GET", f"{API}/space", spaces)

        await connector.run_sync()

        assert set(db.record_groups) == {"10", "20"}
        assert db.record_groups["10"].web_url == f"{BASE}/display/ENG"

    async def test_space_listing_error_syncs_no_spaces_and_writes_no_checkpoints(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/space", json_response({"message": "down"}, status=502))

        await connector.run_sync()

        assert db.record_groups == {}
        assert search.cql == []


class TestAuditPassEdgeCases:
    async def test_blogposts_are_updated_and_other_content_types_ignored(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("blogpost", 0, listing([content("b1", ctype="blogpost")]))
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        search.add("blogpost", 0, listing([]))
        search.add("page", 0, listing([]))

        def ev(obj_id: str, kind: str) -> dict[str, Any]:
            return {"type": {"category": "Pages and Blogs"}, "affectedObjects": [{"type": kind, "id": obj_id}, {"type": "Space", "id": "10"}]}

        atlassian_api.on("GET", AUDIT, {"entities": [ev("b1", "Blog"), ev("p1", "Page"), ev("x", "Attachment")], "pagingInfo": {"lastPage": True}})
        atlassian_api.on("GET", f"{API}/content/b1", content("b1", ctype="blogpost"))
        atlassian_api.on("GET", f"{API}/content/b1/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))
        atlassian_api.on("GET", f"{API}/content/p1", {**content("p1"), "type": "attachment"})
        before = db.records["p1"]

        await connector.run_sync()

        assert db.records["b1"].inherit_permissions is False
        assert db.records["b1"].record_type == RecordType.CONFLUENCE_BLOGPOST
        assert db.records["p1"] is before, "content whose type changed is left as it was"

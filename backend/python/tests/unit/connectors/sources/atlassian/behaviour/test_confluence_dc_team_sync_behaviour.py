"""Confluence Data Center (team) sync, driven end to end over a fake REST API.

The connector, its Confluence client and its request builder are all real; the
Confluence server is an in-memory stub and our databases are in-memory fakes.
The focus is on who ends up able to see what: users, groups, space grants,
page restrictions, and the audit-log pass that catches restriction changes.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    FakeConfigService,
    json_response,
    logged,
    record_logs,
    run_full_sync,
)
from confluence_dc_removal_fakes import RemovalRecordsDb, unlinked_by_full_sync
from fastapi import HTTPException

from app.config.constants.arangodb import (
    AccessRule,
    CollectionNames,
    Connectors,
    OriginTypes,
)
from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)
from app.connectors.sources.atlassian.core import confluence_dc_removal
from app.models.entities import AppUser, AppUserGroup, Record, RecordType, WebpageRecord
from app.models.permission import EntityType, PermissionType
from app.sources.client.confluence.confluence import ConfluenceRESTClientViaToken
from app.sources.external.confluence.confluence import ConfluenceDataSource

CONNECTOR_ID = "conf-dc-team-1"
BASE = "https://confluence.example.com"
API = "/rest/api"
FAKE_PAT = "fake-pat-for-tests"
AUDIT = "/rest/auditing/1.0/events"
ACTIVE_USERS = f"{API}/admin/users/list/active"


class TeamDb(RemovalRecordsDb):
    """Adds the user and group lookups the team connector makes."""

    def __init__(self) -> None:
        super().__init__()
        self.migrations: list[dict[str, Any]] = []
        self.creator_link: Optional[SimpleNamespace] = None

    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> object:
        return next((u for u in reversed(self.app_users) if u.source_user_id == source_user_id), None)

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str) -> object:
        return next((g for g, _ in reversed(self.user_groups) if g.source_user_group_id == external_id), None)

    async def get_all_app_users(self, connector_id: str, *, raise_on_error: bool = False) -> list[Any]:
        return list(self.app_users)

    async def migrate_group_to_user_by_external_id(self, **kwargs: str) -> None:
        self.migrations.append(kwargs)

    async def get_app_user_links(self, connector_id: str) -> list[SimpleNamespace]:
        linked = {u.source_user_id: u for u in self.app_users}
        links = [SimpleNamespace(id=u.id, source_user_id=sid, email=u.email) for sid, u in linked.items()]
        return links + ([self.creator_link] if self.creator_link else [])

    async def get_placeholder_records(self, connector_id: str) -> list[Any]:
        return [r for r in self.records.values() if getattr(r, "is_placeholder", False)]

    def members_of(self, group_name: str) -> Optional[list[str]]:
        for group, members in reversed(self.user_groups):
            if group.name == group_name:
                return sorted(m.email for m in members)
        return None


class TeamStore(FakeCheckpointStore):
    def __init__(self, db: TeamDb) -> None:
        super().__init__()
        self.db = db

    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: dict[str, Any],
        return_fields: list[str] | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        assert collection == CollectionNames.GROUPS.value
        nodes = {g.source_user_group_id: g.to_arango_base_user_group() for g, _ in self.db.user_groups}
        return [n for n in nodes.values() if all(n.get(k) == v for k, v in filters.items())]


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


def unparsable_backfill(request: httpx.Request) -> httpx.Response:
    """The homepage's last-modified lookup reads fine; the full read that backfills it gets a body that is not JSON."""
    if AtlassianApiStub.query(request).get("expand") == "history.lastUpdated,version,space":
        return json_response(content("500"))
    return httpx.Response(200, content=b"<html>proxy error</html>")


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
    data = {"type": "known", "userKey": key, "username": key, "displayName": key.title()}
    if email:
        data["email"] = email
    return data


class ContentSearch:
    """Answers ``/content/search`` per (type, start) and remembers each CQL.

    ``/content``, the space's database listing, holds by default every item the
    search pages have ever returned; ``existing`` replaces that per type.
    """

    def __init__(self) -> None:
        self.pages: dict[tuple[str, int], Any] = {}
        self.cql: list[str] = []
        self.ever_listed: dict[str, dict[str, dict[str, Any]]] = {"page": {}, "blogpost": {}}
        self.existing: dict[str, list[dict[str, Any]] | httpx.Response] = {}
        self.archived: list[dict[str, Any]] | httpx.Response = []

    def add(self, ctype: str, start: int, response: object) -> None:
        self.pages[(ctype, start)] = response
        if isinstance(response, dict):
            for item in response.get("results", []):
                self.ever_listed[ctype][str(item["id"])] = item

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        cql = q["cql"]
        self.cql.append(cql)
        ctype = "page" if cql.startswith("type=page") else "blogpost"
        response = self.pages.get((ctype, int(q.get("start", 0))), listing([]))
        return response if isinstance(response, httpx.Response) else json_response(response)

    def database(self, request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        ctype = q["type"]
        if q.get("status") == "archived":
            items = self.archived if ctype == "page" else []
        else:
            items = self.existing.get(ctype, list(self.ever_listed[ctype].values()))
        if isinstance(items, httpx.Response):
            return httpx.Response(items.status_code, headers=items.headers, content=items.content)
        start, limit = int(q.get("start", 0)), int(q.get("limit", 25))
        page = items[start:start + limit]
        more = start + limit < len(items)
        return json_response(listing(page, next_start=start + limit if more else None))


@pytest.fixture
def search(atlassian_api: AtlassianApiStub) -> ContentSearch:
    handler = ContentSearch()
    atlassian_api.on("GET", f"{API}/content/search", handler)
    atlassian_api.on("GET", f"{API}/content", handler.database)
    return handler


def with_directory(api: AtlassianApiStub, users: list[dict[str, Any]], groups: dict[str, list[dict[str, Any]]]) -> None:
    api.on("GET", ACTIVE_USERS, {"results": users})
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
    api.on("GET", f"{API}/server-information", {"version": "10.2.0", "versionNumbers": [10, 2, 0]})
    api.on("GET", ACTIVE_USERS, {"results": []})
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
            "GET", ACTIVE_USERS, lambda r: json_response(pages[AtlassianApiStub.query(r).get("start", "0")])
        )

        await connector._sync_users()

        emails = {u.email for u in db.app_users}
        assert len(emails) == 200 and "last@example.com" in emails
        assert all(u.source_user_id != "no-mail" for u in db.app_users)
        assert [AtlassianApiStub.query(r)["start"] for r in atlassian_api.calls("GET", ACTIVE_USERS)] == ["0", "200"]
        assert {m["group_external_id"] for m in db.migrations} >= {"u0", "last"}, (
            "access held by a stand-in group for an email-less user moves to the real user once their email is known"
        )

    @pytest.mark.parametrize("version", ["9.3.0", "10.1.0", "11.1.0"])
    async def test_a_disabled_account_with_an_active_users_address_is_not_linked(
        self, atlassian_api, db, store, version
    ) -> None:
        # /user/list returns disabled accounts too; Data Center allows duplicate emails.
        connector = await make_connector(atlassian_api, db, store)
        numbers = [int(part) for part in version.split(".")]
        atlassian_api.on("GET", f"{API}/server-information", {"version": version, "versionNumbers": numbers})
        ana, disabled = user("ana", "ana@example.com"), user("ana-disabled", "ana@example.com")
        atlassian_api.on("GET", f"{API}/user/list", {"results": [ana, disabled]})
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana]})

        await connector._sync_users()

        assert [u.source_user_id for u in db.app_users] == ["ana"]
        assert atlassian_api.calls("GET", f"{API}/user/list") == []

    @pytest.mark.parametrize("version", ["8.5.4", "9.0.0", "9.2.1"])
    async def test_before_9_3_the_listed_users_are_linked(self, atlassian_api, db, store, version) -> None:
        # These docs give no per-user status, so a disabled duplicate is linked too: a known limit, exact from 9.3.
        connector = await make_connector(atlassian_api, db, store)
        numbers = [int(part) for part in version.split(".")]
        atlassian_api.on("GET", f"{API}/server-information", {"version": version, "versionNumbers": numbers})
        ana, disabled = user("ana", "ana@example.com"), user("ana-disabled", "ana@example.com")
        atlassian_api.on("GET", f"{API}/user/list", {"results": [ana, disabled]})
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana]})

        await connector._sync_users()

        assert [u.source_user_id for u in db.app_users] == ["ana", "ana-disabled"]
        assert atlassian_api.calls("GET", ACTIVE_USERS) == []
        assert db.withdrawn_users == []

    @pytest.mark.parametrize("info", [
        {},
        {"versionNumbers": ["x"], "version": "x"},
        json_response({}, status=500),
    ])
    async def test_an_unreadable_version_links_no_user(self, atlassian_api, db, store, info) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/server-information", info)
        atlassian_api.on("GET", f"{API}/user/list", {"results": [user("ana", "ana@example.com")]})
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [user("ana", "ana@example.com")]})
        logs = record_logs(connector)

        await connector._sync_users()

        assert db.app_users == []
        assert atlassian_api.calls("GET", ACTIVE_USERS) == [] and atlassian_api.calls("GET", f"{API}/user/list") == []
        assert any("version could not be read" in m for m in logged(logs))

    async def test_a_version_that_could_not_be_read_is_asked_for_again_on_the_next_sync(
        self, atlassian_api, db, store
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/server-information", json_response({}, status=503))
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [user("ana", "ana@example.com")]})
        await connector._sync_users()
        assert db.app_users == []

        atlassian_api.on("GET", f"{API}/server-information", {"version": "10.2.0", "versionNumbers": [10, 2, 0]})
        await connector._sync_users()

        assert [u.source_user_id for u in db.app_users] == ["ana"]

    @pytest.mark.parametrize("version", ["11.2.0", "12.0.0"])
    async def test_a_newer_release_that_answers_the_active_list_links_its_users(
        self, atlassian_api, db, store, version
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        numbers = [int(part) for part in version.split(".")]
        atlassian_api.on("GET", f"{API}/server-information", {"version": version, "versionNumbers": numbers})
        ana, disabled = user("ana", "ana@example.com"), user("ana-disabled", "ana@example.com")
        atlassian_api.on("GET", f"{API}/user/list", {"results": [ana, disabled]})
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana]})

        await connector._sync_users()

        assert [u.source_user_id for u in db.app_users] == ["ana"]

    @pytest.mark.parametrize(("answer", "why"), [
        (json_response({"message": "not found"}, status=404), "404"),
        (json_response({"message": "forbidden"}, status=403), "403"),
        ({"users": [{"type": "known", "userKey": "ana", "email": "ana@example.com"}]}, "not a page of results"),
        (json_response([]), "not a page of results"),
    ])
    async def test_a_newer_release_that_refuses_the_active_list_links_and_withdraws_no_user(
        self, atlassian_api, db, store, answer, why
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        db.app_users.append(AppUser(
            app_name=Connectors.CONFLUENCE_DATA_CENTER, connector_id=CONNECTOR_ID, source_user_id="bob",
            org_id="org-1", email="bob@example.com", full_name="Bob",
        ))
        atlassian_api.on("GET", f"{API}/server-information", {"version": "11.2.0", "versionNumbers": [11, 2, 0]})
        atlassian_api.on("GET", f"{API}/user/list", {"results": [user("ana", "ana@example.com")]})
        atlassian_api.on("GET", ACTIVE_USERS, answer)
        logs = record_logs(connector)

        await connector._sync_users()

        assert [u.source_user_id for u in db.app_users] == ["bob"], "nothing linked"
        assert db.withdrawn_users == [], "a refused list is not a complete one"
        assert atlassian_api.calls("GET", f"{API}/user/list") == []
        assert any(why in m for m in logged(logs))

    async def test_a_linked_user_the_active_list_no_longer_shows_is_withdrawn(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        db.creator_link = SimpleNamespace(id="creator-node", source_user_id="creator_admin-user-1", email="owner@example.com")
        ana, bob = user("ana", "ana@example.com"), user("bob", "bob@example.com")
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana, bob]})
        await connector._sync_users()
        assert db.withdrawn_users == []

        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana]})   # bob was disabled
        await connector._sync_users()

        assert [u.source_user_id for u in db.withdrawn_users] == ["bob"]

        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana, bob]})   # and enabled again
        await connector._sync_users()
        assert db.app_users[-1].source_user_id == "bob"

    async def test_an_active_list_entry_without_a_key_withdraws_nobody(self, atlassian_api, db, store) -> None:
        """The 9.3-10.0 docs list no fields for an entry: one without a userKey may be a linked user, so nobody the
        list seems to leave out is withdrawn."""
        connector = await make_connector(atlassian_api, db, store)
        ana, bob = user("ana", "ana@example.com"), user("bob", "bob@example.com")
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana, bob]})
        await connector._sync_users()

        keyless_bob = {k: v for k, v in bob.items() if k not in ("userKey", "key")}
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana, keyless_bob]})
        await connector._sync_users()

        assert db.withdrawn_users == []

    @pytest.mark.parametrize("second_page", [
        json_response({"message": "busy"}, status=503),
        {"oops": True},
        # an empty page that still links a next one is not the end of the list
        {"results": [], "size": 0, "_links": {"base": BASE, "next": f"{ACTIVE_USERS}?start=2"}},
    ])
    async def test_a_cut_active_list_withdraws_nobody(self, atlassian_api, db, store, second_page) -> None:
        connector = await make_connector(atlassian_api, db, store)
        ana, bob = user("ana", "ana@example.com"), user("bob", "bob@example.com")
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [ana, bob]})
        await connector._sync_users()

        pages = {"0": {"results": [ana], "size": 1, "_links": {"base": BASE, "next": f"{ACTIVE_USERS}?start=1"}}}
        atlassian_api.on(
            "GET", ACTIVE_USERS,
            lambda r: (lambda page: page if isinstance(page, httpx.Response) else json_response(page))(
                pages.get(AtlassianApiStub.query(r).get("start", "0"), second_page)
            ),
        )
        await connector._sync_users()

        assert db.withdrawn_users == []

    async def test_an_empty_active_list_withdraws_nobody(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [user("ana", "ana@example.com")]})
        await connector._sync_users()

        atlassian_api.on("GET", ACTIVE_USERS, {"results": []})
        await connector._sync_users()

        assert db.withdrawn_users == []

    async def test_an_entry_that_is_not_a_known_user_is_not_linked(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        untyped = {k: v for k, v in user("untyped", "untyped@example.com").items() if k != "type"}
        atlassian_api.on("GET", ACTIVE_USERS, {"results": [
            user("ana", "ana@example.com"),
            {**user("odd", "odd@example.com"), "type": "something-new"},
            {**user("anon", "anon@example.com"), "type": "anonymous"},
            untyped,
        ]})

        await connector._sync_users()

        assert [u.source_user_id for u in db.app_users] == ["ana"]

    async def test_a_user_enabled_again_is_linked_on_the_next_sync(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        await connector._sync_users()
        assert db.app_users == []

        atlassian_api.on("GET", ACTIVE_USERS, {"results": [user("ana", "ana@example.com")]})
        await connector._sync_users()

        assert [u.source_user_id for u in db.app_users] == ["ana"]

    async def test_a_capped_page_of_active_users_is_followed_by_the_rows_it_returned(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        alice, bob = user("alice", "alice@example.com"), user("bob", "bob@example.com")
        pages = {
            "0": {"results": [alice], "size": 1, "_links": {"base": BASE, "next": f"{ACTIVE_USERS}?start=200&limit=200"}},
            "1": {"results": [bob], "size": 1, "_links": {"base": BASE}},
        }
        atlassian_api.on("GET", ACTIVE_USERS, lambda r: json_response(pages[AtlassianApiStub.query(r).get("start", "0")]))

        await connector._sync_users()

        assert sorted(u.email for u in db.app_users) == ["alice@example.com", "bob@example.com"]

    async def test_group_members_are_read_to_the_end_and_missing_emails_are_looked_up(self, atlassian_api, db, store) -> None:
        people = [user(f"m{i}", f"m{i}@example.com") for i in range(200)] + [user("ldap", "ldap@example.com")]
        connector = await make_connector(atlassian_api, db, store)
        user_pages = {"0": people[:200], "200": people[200:]}
        atlassian_api.on(
            "GET", ACTIVE_USERS,
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

    async def test_a_failed_user_lookup_does_not_empty_the_group(self, atlassian_api, db, store) -> None:
        ana = user("ana", "ana@example.com")
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ana], {"eng": [ana]})
        await connector._sync_users()
        await connector._sync_user_groups()
        saves = len(db.user_groups)
        assert db.members_of("eng") == ["ana@example.com"]

        db.get_all_app_users = AsyncMock(side_effect=ConnectionError("database unavailable"))
        await connector._sync_user_groups()

        assert len(db.user_groups) == saves, "the group is not saved when its members could not be looked up"
        assert db.members_of("eng") == ["ana@example.com"]

    async def test_a_lookup_that_finds_no_users_still_empties_the_group(self, atlassian_api, db, store) -> None:
        ana = user("ana", "ana@example.com")
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ana], {"eng": [ana]})
        await connector._sync_users()
        await connector._sync_user_groups()
        assert db.members_of("eng") == ["ana@example.com"]

        db.app_users.clear()
        await connector._sync_user_groups()

        assert db.user_groups[-1][0].name == "eng"
        assert db.members_of("eng") == []

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


    async def test_a_failed_email_lookup_does_not_drop_the_member(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(
            atlassian_api,
            [user("alice", "alice@example.com"), user("ldap", "ldap@example.com")],
            {"eng": [user("alice", "alice@example.com"), user("ldap", None)]},
        )
        atlassian_api.on("GET", f"{API}/user", json_response(user("ldap", "ldap@example.com")))
        await connector._sync_users()
        await connector._sync_user_groups()
        assert db.members_of("eng") == ["alice@example.com", "ldap@example.com"]

        atlassian_api.on("GET", f"{API}/user", json_response({"message": "busy"}, status=503))
        await connector._sync_user_groups()

        assert db.members_of("eng") == ["alice@example.com", "ldap@example.com"]

    async def test_an_unauthorized_email_lookup_does_not_drop_the_member(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(
            atlassian_api,
            [user("alice", "alice@example.com"), user("ldap", "ldap@example.com")],
            {"eng": [user("alice", "alice@example.com"), user("ldap", None)]},
        )
        atlassian_api.on("GET", f"{API}/user", json_response(user("ldap", "ldap@example.com")))
        await connector._sync_users()
        await connector._sync_user_groups()
        saves_before = len(db.user_groups)

        atlassian_api.on("GET", f"{API}/user", json_response({"message": "token expired"}, status=401))
        await connector._sync_user_groups()

        assert db.members_of("eng") == ["alice@example.com", "ldap@example.com"]
        assert len(db.user_groups) == saves_before, "the group is not saved again with a shorter list"

    async def test_a_group_that_disappears_part_way_through_its_members_ends_up_empty(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        people = [user(f"m{i}", f"m{i}@example.com") for i in range(200)]

        def users(request: httpx.Request) -> httpx.Response:
            return json_response({"results": people if AtlassianApiStub.query(request).get("start", "0") == "0" else []})

        def members(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("start", "0") == "0":
                return json_response({"results": people})
            return json_response({"message": "no group"}, status=404)

        atlassian_api.on("GET", ACTIVE_USERS, users)
        atlassian_api.on("GET", f"{API}/group", {"results": [{"type": "group", "name": "eng"}]})
        atlassian_api.on("GET", f"{API}/group/eng/member", members)
        await connector._sync_users()

        await connector._sync_user_groups()

        assert db.members_of("eng") == [], "a deleted group keeps no members"


    async def test_short_pages_that_say_more_follow_are_followed(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com"), user("bob", "bob@example.com")], {})
        await connector._sync_users()

        def paged(first: list, second: list, path: str) -> Callable[[httpx.Request], httpx.Response]:
            def handler(request: httpx.Request) -> httpx.Response:
                if AtlassianApiStub.query(request).get("start", "0") == "0":
                    return json_response({"results": first, "size": len(first), "_links": {"base": BASE, "next": f"{path}?start=1"}})
                return json_response({"results": second, "size": len(second), "_links": {"base": BASE}})
            return handler

        atlassian_api.on("GET", f"{API}/group", paged(
            [{"type": "group", "name": "ops"}], [{"type": "group", "name": "eng"}], "/rest/api/group",
        ))
        atlassian_api.on("GET", f"{API}/group/ops/member", {"results": [], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/group/eng/member", paged(
            [user("alice", "alice@example.com")], [user("bob", "bob@example.com")], "/rest/api/group/eng/member",
        ))

        await connector._sync_user_groups()

        assert db.members_of("ops") == []
        assert db.members_of("eng") == ["alice@example.com", "bob@example.com"], (
            "eng is on the second page of groups, and bob on the second page of its members"
        )

    async def test_a_capped_member_page_is_followed_by_the_rows_it_returned(self, atlassian_api, db, store) -> None:
        # Data Center caps a page below the limit but still links to start + limit (CONFSERVER-95272).
        connector = await make_connector(atlassian_api, db, store)
        alice, bob, eve = user("alice", "alice@example.com"), user("bob", "bob@example.com"), user("eve", "eve@example.com")
        with_directory(atlassian_api, [alice, bob, eve], {"eng": []})
        await connector._sync_users()
        pages = {
            "0": {"results": [alice], "size": 1, "_links": {"base": BASE, "next": "/rest/api/group/eng/member?start=200&limit=200"}},
            "1": {"results": [bob], "size": 1, "_links": {"base": BASE}},
            "200": {"results": [eve], "size": 1, "_links": {"base": BASE}},
        }
        atlassian_api.on("GET", f"{API}/group/eng/member", lambda r: json_response(pages[AtlassianApiStub.query(r).get("start", "0")]))

        await connector._sync_user_groups()

        assert db.members_of("eng") == ["alice@example.com", "bob@example.com"]

    async def test_a_failure_after_a_short_member_page_keeps_the_stored_members(self, atlassian_api, db, store) -> None:
        connector = await make_connector(atlassian_api, db, store)
        alice, bob = user("alice", "alice@example.com"), user("bob", "bob@example.com")
        with_directory(atlassian_api, [alice, bob], {"eng": [alice, bob]})
        await connector._sync_users()
        await connector._sync_user_groups()
        assert db.members_of("eng") == ["alice@example.com", "bob@example.com"]

        def members(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("start", "0") == "0":
                return json_response({"results": [alice], "size": 1, "_links": {"base": BASE, "next": "/rest/api/group/eng/member?start=1"}})
            return json_response({"message": "busy"}, status=503)

        atlassian_api.on("GET", f"{API}/group/eng/member", members)
        await connector._sync_user_groups()

        assert db.members_of("eng") == ["alice@example.com", "bob@example.com"]


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

    @pytest.mark.parametrize(
        "groups",
        [
            pytest.param([{"type": "group", "id": "g-uuid-2", "name": "readers"}], id="no-group-has-the-name"),
            pytest.param(
                [{"type": "group", "id": "g-uuid-1", "name": "writers"}, {"type": "group", "id": "g-uuid-2", "name": "writers"}],
                id="name-is-ambiguous",
            ),
        ],
    )
    async def test_group_name_without_a_single_match_grants_nothing(
        self, atlassian_api, db, store, search, groups
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/group", {"results": groups})
        for g in groups:
            atlassian_api.on("GET", f"{API}/group/{g['name']}/member", {"results": []})
        atlassian_api.on(
            "GET",
            f"{API}/space/ENG/permissions",
            json_response([{"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "group", "name": "writers"}}]),
        )

        await connector.run_sync()

        assert db.record_group_permissions.get("10", []) == []

    async def test_same_group_name_in_another_connector_grants_nothing(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        db.user_groups.append(
            (AppUserGroup(app_name=Connectors.CONFLUENCE_DATA_CENTER, connector_id="other-connector",
                          source_user_group_id="other-uuid", name="writers", org_id="org-1"), [])
        )
        atlassian_api.on(
            "GET",
            f"{API}/space/ENG/permissions",
            json_response([{"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "group", "name": "writers"}}]),
        )

        await connector.run_sync()

        assert db.record_group_permissions.get("10", []) == []

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


def page_stub(cid: str) -> WebpageRecord:
    """What the processor writes for a parent no sync has read yet."""
    return WebpageRecord(
        org_id="org-1", record_name=cid, record_type=RecordType.CONFLUENCE_PAGE, external_record_id=cid,
        connector_name=Connectors.CONFLUENCE_DATA_CENTER, connector_id=CONNECTOR_ID, origin=OriginTypes.CONNECTOR,
        version=0, is_placeholder=True, access_rule=AccessRule.RESTRICTED, external_record_group_id="10",
    )


class TestPlaceholderSweep:
    async def test_a_parent_the_filters_left_out_is_filled_in_up_to_the_first_real_ancestor(
        self, atlassian_api, db, store
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        await connector._sync_users()
        parent, grandparent = page_stub("10"), page_stub("5")
        db.records.update({"10": parent, "5": grandparent, "404": page_stub("404")})
        seeds = [parent, db.records["404"]]
        db.get_placeholder_records = AsyncMock(return_value=seeds)
        atlassian_api.on("GET", f"{API}/content/10", {**content("10", version=3), "ancestors": [{"id": "5", "type": "page"}]})
        atlassian_api.on("GET", f"{API}/content/5", content("5"))
        atlassian_api.on(
            "GET", f"{API}/content/5/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}])
        )

        await connector._sweep_placeholder_records("org-1")

        page, top, gone = db.records["10"], db.records["5"], db.records["404"]
        assert page.record_name == "Title 10" and page.id == parent.id
        assert page.access_rule == AccessRule.STRICT, "an unrestricted parent no longer hides the pages under it"
        assert page.is_placeholder is True, "out-of-scope content is still never indexed"
        assert (page.parent_external_record_id, page.parent_record_type) == ("5", RecordType.CONFLUENCE_PAGE)
        assert top.record_name == "Title 5" and top.access_rule == AccessRule.RESTRICTED
        assert [p.email for p in db.record_permissions["5"]] == ["alice@example.com"]
        assert gone.access_rule == AccessRule.RESTRICTED and db.record_permissions["404"] == [], (
            "an ancestor the source no longer returns fails closed"
        )

    async def test_a_full_sync_ends_with_the_sweep(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        db.records["10"] = page_stub("10")
        atlassian_api.on("GET", f"{API}/content/10", content("10"))

        await connector.run_sync()

        assert db.records["10"].record_name == "Title 10"
        assert db.records["10"].access_rule == AccessRule.STRICT


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
        assert page.access_rule == AccessRule.RESTRICTED, "space members must not see a restricted page"
        assert page.inherit_permissions is True, "a RESTRICTED page that does not inherit is hidden from everyone"
        grants = {(p.entity_type, p.email or p.external_id) for p in db.record_permissions["p1"]}
        assert grants == {(EntityType.USER, "alice@example.com"), (EntityType.GROUP, "contractor")}
        stand_in = [g for g, _ in db.user_groups if g.source_user_group_id == "contractor"]
        assert stand_in, "a user with no email yet keeps access through a stand-in group"
        assert db.records["open"].access_rule == AccessRule.STRICT
        assert db.record_permissions["open"] == []

    async def test_a_failed_restriction_lookup_does_not_open_up_a_restricted_page(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1")]))
        restriction = f"{API}/content/p1/restriction/relevantViewRestrictions"
        atlassian_api.on("GET", restriction, restricted_to(users=[{"userKey": "alice"}]))
        await connector.run_sync()
        assert db.records["p1"].access_rule == AccessRule.RESTRICTED

        atlassian_api.on("GET", restriction, json_response({"message": "rate limited"}, status=429))
        await connector.run_sync()

        assert db.records["p1"].access_rule == AccessRule.RESTRICTED

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

        assert db.records["p1"].access_rule == AccessRule.RESTRICTED
        for dependent in ("att1", "c1"):
            record = db.records[dependent]
            assert (record.inherit_permissions, record.parent_external_record_id) == (True, "p1"), (
                f"{dependent} inherits from its page, never straight from the space"
            )
            assert db.record_permissions[dependent] == []

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

        assert db.records["p1"].access_rule == AccessRule.RESTRICTED
        assert [(p.entity_type, p.external_id) for p in db.record_permissions["p1"]] == [(EntityType.GROUP, "finance")]


    async def test_page_restricted_only_to_a_group_we_have_not_synced_is_not_opened(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")]))
        atlassian_api.on(
            "GET",
            f"{API}/content/p1/restriction/relevantViewRestrictions",
            restricted_to(groups=[{"type": "group", "name": "new-hires"}]),
        )

        await connector.run_sync()

        assert db.records["p1"].access_rule == AccessRule.RESTRICTED, "space members must not see it"


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
        search.existing["page"] = [content("p1"), content("500")]

        await connector.run_sync()

        home = db.records["500"]
        assert home.access_rule == AccessRule.RESTRICTED
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
            assert db.record_permissions[rid] == [], rid


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
        assert db.records["p1"].access_rule == AccessRule.STRICT
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
        db.records["gone"] = db.records["p2"].model_copy(update={"id": "gone-id", "external_record_id": "gone"})
        atlassian_api.on("GET", f"{API}/content/gone", json_response({"message": "no"}, status=404))

        await connector.run_sync()

        audit_calls = atlassian_api.calls("GET", AUDIT)
        assert [AtlassianApiStub.query(r).get("pageCursor") for r in audit_calls] == [None, "cur-2"]
        assert AtlassianApiStub.query(audit_calls[0])["categories"] == "Pages and Blogs"
        assert db.records["p1"].access_rule == AccessRule.RESTRICTED
        assert [p.email for p in db.record_permissions["p1"]] == ["alice@example.com"]
        assert "filtered-out-page" not in db.records, "pages excluded by filters are not created by the audit pass"
        assert db.records["p2"].access_rule == AccessRule.STRICT, "space-level 'Permissions' events are not page restrictions"
        assert audit_key(store)["last_sync_time_ms"] > first_clock

    async def test_a_page_restricted_later_takes_its_stored_files_and_comments_with_it(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [user("alice", "alice@example.com")], {})
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")])]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/attachment", {"results": [attachment("c1file")], "_links": {"base": BASE}})
        await connector.run_sync()
        assert db.records["p1"].access_rule == AccessRule.STRICT
        assert all(db.record_permissions[k] == [] for k in ("att1", "c1", "c1file"))

        search.add("page", 0, listing([]))
        event = {"type": {"category": "Pages and Blogs"}, "affectedObjects": [{"type": "Page", "id": "p1"}, {"type": "Space", "id": "10"}]}
        atlassian_api.on("GET", AUDIT, {"entities": [event], "pagingInfo": {"lastPage": True}})
        atlassian_api.on("GET", f"{API}/content/p1", {**content("p1"), "type": "page"})
        atlassian_api.on("GET", f"{API}/content/p1/restriction/relevantViewRestrictions", restricted_to(users=[{"userKey": "alice"}]))
        await connector.run_sync()

        assert db.records["p1"].access_rule == AccessRule.RESTRICTED
        updated = {
            r.external_record_id: (r.inherit_permissions, r.parent_external_record_id, [p.email for p in perms])
            for r, perms in db.permission_updates
        }
        assert updated == {
            "att1": (True, "p1", []),
            "c1": (True, "p1", []),
            "c1file": (True, "c1", []),
        }, "the page's files and comments keep inheriting from the page and carry no grant of their own"

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

        stored_version = db.records["p1"].version
        await connector.reindex_records([db.records[k] for k in ("p1", "b1", "c2", "att1", "c2file")])

        updated = {r.external_record_id: r for r in db.content_updates}
        assert set(updated) == {"p1", "c2", "att1", "c2file"}
        assert updated["p1"].version == stored_version + 1
        assert updated["p1"].access_rule == AccessRule.RESTRICTED
        assert (updated["c2"].parent_external_record_id, updated["c2"].parent_record_type) == ("c1", RecordType.COMMENT)
        assert (updated["c2file"].parent_external_record_id, updated["c2file"].parent_record_type) == ("c2", RecordType.COMMENT)
        restricted = {r.external_record_id for r, perms in db.permission_updates if [p.email for p in perms] == ["alice@example.com"]}
        assert restricted == {"p1"}
        inherited = {r.external_record_id for r, perms in db.permission_updates if perms == []}
        assert {"c2", "att1", "c2file"} <= inherited
        assert {k: (updated[k].inherit_permissions, updated[k].parent_external_record_id) for k in ("c2", "att1", "c2file")} == {
            "c2": (True, "c1"), "att1": (True, "p1"), "c2file": (True, "c2"),
        }, "a restricted page's files and comments inherit from the record above them, not from the space, on reindex"
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
        assert perms == []
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

    async def test_date_filters_reach_the_search_as_set(self, atlassian_api, db, store, search) -> None:
        def ms(*parts: int) -> int:
            return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)

        filters = _filters(sync={
            "modified": {"operator": "is_between", "type": "datetime", "value": {"start": ms(2024, 3, 1, 9, 30), "end": ms(2024, 6, 1, 18)}},
            "created": {"operator": "is_between", "type": "datetime", "value": {"start": ms(2023, 1, 1), "end": ms(2024, 2, 1, 12)}},
        })
        connector = await make_connector(atlassian_api, db, store, filters=filters)
        search.add("page", 0, listing([content("p1")]))

        await connector.run_sync()

        for ctype in ("page", "blogpost"):
            cql = next(c for c in search.cql if c.startswith(f"type={ctype}"))
            assert 'lastModified > "2024-03-01 09:30"' in cql
            assert 'lastModified < "2024-06-01 18:00"' in cql, "not a day early"
            assert 'created > "2023-01-01 00:00"' in cql
            assert 'created < "2024-02-01 12:00"' in cql, "not a day early"

    async def test_the_later_of_the_modified_filter_and_the_looked_back_checkpoint_bounds_the_search(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        checkpoint = store.values_for("confluence_pages/ENG")["last_sync_time"]
        checkpoint_at = datetime.strptime(checkpoint, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)

        inside_look_back = checkpoint_at - timedelta(hours=12)
        connector.config_service.config["filters"] = _filters(sync={
            "modified": {"operator": "is_after", "type": "datetime", "value": {"start": int(inside_look_back.timestamp() * 1000)}},
        })
        search.cql.clear()
        await connector.run_sync()

        cql = next(c for c in search.cql if c.startswith("type=page"))
        assert f'lastModified > "{inside_look_back.strftime("%Y-%m-%d %H:%M")}"' in cql, "nothing from before the filter date"
        assert cql.count("lastModified >") == 1

    async def test_switched_off_pages_and_files_are_kept_but_not_indexed(self, atlassian_api, db, store, search) -> None:
        filters = _filters(indexing={"pages": False, "page_attachments": False})
        connector = await make_connector(atlassian_api, db, store, filters=filters)
        search.add("page", 0, listing([content("p1", attachments=[attachment("att1")])]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/attachment", {"results": [attachment("c1file")], "_links": {"base": BASE}})

        await connector.run_sync()

        for rid in ("p1", "att1", "c1file"):
            assert db.records[rid].indexing_status == "AUTO_INDEX_OFF", rid

    async def test_switching_off_comment_indexing_is_respected(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store, filters=_filters(indexing={"page_comments": False}))
        search.add("page", 0, listing([content("p1")]))
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/attachment", {"results": [attachment("c1file")], "_links": {"base": BASE}})

        await connector.run_sync()

        assert db.records["c1"].indexing_status == "AUTO_INDEX_OFF"
        assert db.records["p1"].indexing_status != "AUTO_INDEX_OFF"
        assert db.records["c1file"].indexing_status != "AUTO_INDEX_OFF", "a comment's files follow the attachments switch"

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

        assert db.records["b1"].access_rule == AccessRule.RESTRICTED
        assert db.records["b1"].record_type == RecordType.CONFLUENCE_BLOGPOST
        assert db.records["p1"] is before, "content whose type changed is left as it was"


def count_group_reads(db: TeamDb) -> list[str]:
    """Records each first-page read of a space's stored records."""
    reads: list[str] = []
    original = db.get_records_in_record_group

    async def counting(
        connector_id: str, external_group_id: str, limit: int, after_key: Optional[str] = None, **kwargs: object
    ) -> list[Record]:
        if after_key is None:
            reads.append(external_group_id)
        return await original(connector_id, external_group_id, limit, after_key, **kwargs)

    db.get_records_in_record_group = counting
    return reads


def child_of(cid: str, parent: str) -> dict[str, Any]:
    return {**content(cid), "ancestors": [{"id": parent}]}


class TestRemovalFromSource:
    """What leaves Confluence, or the sync filters, leaves PipesHub; a failed read removes nothing."""

    @staticmethod
    async def _two_pages_synced(atlassian_api, db, store, search, filters=None) -> ConfluenceDataCenterConnector:
        connector = await make_connector(atlassian_api, db, store, filters=filters)
        atlassian_api.on("GET", f"{API}/content/p2/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1"), content("p2", attachments=[attachment("a2")]), child_of("p3", "p2")]))
        await connector.run_sync()
        assert {"p1", "p2", "a2", "c1", "p3"} <= set(db.records)
        assert store.values_for("confluence_pages/ENG")["last_sync_time"]
        search.add("page", 0, listing([]))
        return connector

    @staticmethod
    def _hold_checkpoint(store) -> str:
        key = next(k for k in store.sync_points if k.endswith("confluence_pages/ENG"))
        store.sync_points[key]["last_sync_time"] = "2024-01-01T00:00:00.000Z"
        return "2024-01-01T00:00:00.000Z"

    async def test_a_trashed_page_leaves_with_its_files_and_comments_and_its_child_page_stays(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.existing["page"] = [content("p1"), child_of("p3", "p1")]

        await connector.run_sync()

        assert not {"p2", "a2", "c1"} & set(db.records)
        assert db.records["p3"].parent_external_record_id is None, "the child page moves to the space root"
        assert "p1" in db.records

    async def test_a_trashed_page_and_its_trashed_comment_the_source_no_longer_has_are_removed(
        self, atlassian_api, db, store, search
    ) -> None:
        """The scans list the trash and the delete accepts a trashed root, so nothing stays behind for good."""
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        for external_id in ("p2", "c1"):
            db.records[external_id].is_deleted = True
        search.existing["page"] = [content("p1"), child_of("p3", "p1")]

        await connector.run_sync()

        assert not {"p2", "a2", "c1"} & set(db.records)
        assert {"p1", "p3"} <= set(db.records)

    async def test_a_space_with_a_trashed_page_finishes_its_removal(self, atlassian_api, db, store, search) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        db.records["p2"].is_deleted = True
        atlassian_api.on("GET", f"{API}/space", {"results": [space("HR", 20)], "_links": {"base": BASE}})

        await connector.run_sync()

        assert "10" not in db.record_groups
        assert not any(r.external_record_group_id == "10" for r in db.records.values())
        scope = store.values_for("confluence_space_scope/all")
        assert (scope["space_ids"], scope["pending"]) == (["20"], []), "the removal finished"

    async def test_a_page_the_account_can_no_longer_see_is_removed_and_returns_when_visible_again(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.existing["page"] = [content("p2"), child_of("p3", "p2")]
        await connector.run_sync()
        assert "p1" not in db.records

        # Regaining access changes nothing Confluence dates, so the change search still leaves it out.
        search.existing["page"] = [content("p1"), content("p2"), child_of("p3", "p2")]
        search.cql.clear()

        def by_id(request: httpx.Request) -> httpx.Response:
            cql = AtlassianApiStub.query(request)["cql"]
            search.cql.append(cql)
            found = [content("p1")] if "id in (p1)" in cql else []
            return json_response(listing(found))

        atlassian_api.on("GET", f"{API}/content/search", by_id)
        await connector.run_sync()

        assert "p1" in db.records
        asked = [c for c in search.cql if "id in (p1)" in c]
        assert asked and all("lastModified >" not in c for c in asked)

    async def test_a_page_synced_by_id_brings_back_only_itself_not_its_subtree(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.existing["page"] = [content("p1"), child_of("p3", "p1")]
        await connector.run_sync()
        assert "p2" not in db.records
        search.existing["page"] = [content("p1"), content("p2"), child_of("p3", "p2")]
        search.cql.clear()
        await connector.run_sync()
        by_id = [c for c in search.cql if "id in (p2)" in c]
        assert by_id and all("ancestor in" not in c for c in by_id)

    async def test_a_homepage_the_filters_leave_out_is_not_backfilled(self, atlassian_api, db, store, search) -> None:
        filters = {"sync": {"values": {"page_ids": {"operator": "in", "type": "list", "value": ["p1"]}}}}
        connector = await make_connector(atlassian_api, db, store, filters=filters)

        def spaces(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("expand") == "homepage":
                return json_response({"results": [{**space("ENG", 10), "homepage": {"id": 500, "title": "Home"}}]})
            return json_response({"results": [space("ENG", 10)], "_links": {"base": BASE}})

        atlassian_api.on("GET", f"{API}/space", spaces)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        search.add("page", 0, listing([content("p1")]))
        search.existing["page"] = [content("p1"), content("500")]

        await connector.run_sync()
        await connector.run_sync()

        assert "500" not in db.records
        assert db.deleted == [], "not created by the backfill and then removed by the filter check"

    async def test_an_archived_page_the_account_can_see_is_kept(self, atlassian_api, db, store, search) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.existing["page"] = [content("p2"), child_of("p3", "p2")]
        search.archived = [content("p1")]

        await connector.run_sync()

        assert "p1" in db.records

    async def test_a_400_past_the_first_archived_page_removes_nothing(self, atlassian_api, db, store, search) -> None:
        """Only a first-page 400 means "this server has no archived pages"."""
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.existing["page"] = [content("p2"), child_of("p3", "p2")]
        # A full first archived page, then a 400 where p1 would have been listed.
        search.archived = [content(f"a{i}") for i in range(confluence_dc_removal.CONTENT_LIST_LIMIT)] + [content("p1")]
        served = search.database

        def archived_fails_past_the_first_page(request: httpx.Request) -> httpx.Response:
            q = AtlassianApiStub.query(request)
            if q.get("status") == "archived" and int(q.get("start", 0)) > 0:
                return json_response({"message": "bad request"}, status=400)
            return served(request)

        atlassian_api.on("GET", f"{API}/content", archived_fails_past_the_first_page)

        await connector.run_sync()

        assert "p1" in db.records, "an archived page past a failed listing page is not treated as gone"

    async def test_a_server_without_archived_pages_still_removes_a_deleted_page(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.existing["page"] = [content("p2"), child_of("p3", "p2")]
        search.archived = json_response({"message": "unknown status"}, status=400)

        await connector.run_sync()

        assert "p1" not in db.records

    @pytest.mark.parametrize("answer", [
        pytest.param(json_response({"message": "busy"}, status=503), id="server-error"),
        pytest.param(json_response({"message": "no results list"}), id="no-results-list"),
        pytest.param(json_response(listing([], next_start=25)), id="empty-page-pointing-further"),
    ])
    async def test_a_listing_that_cannot_be_read_removes_nothing_and_holds_the_checkpoint(
        self, atlassian_api, db, store, search, answer
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        held = self._hold_checkpoint(store)
        search.add("page", 0, listing([content("p1", version=2)]))
        search.existing["page"] = answer

        await connector.run_sync()

        assert {"p1", "p2", "p3"} <= set(db.records)
        assert store.values_for("confluence_pages/ENG")["last_sync_time"] == held

    async def test_a_failed_record_read_removes_nothing(self, atlassian_api, db, store, search) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.existing["page"] = [content("p1")]
        db.fail_scan = True

        await connector.run_sync()

        assert {"p2", "p3"} <= set(db.records)

    async def test_a_failed_delete_holds_the_checkpoint_and_the_next_sync_finishes_it(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        held = self._hold_checkpoint(store)
        search.add("page", 0, listing([content("p1", version=2)]))
        search.existing["page"] = [content("p1"), child_of("p3", "p1")]
        db.fail_delete_for = {"p2"}

        await connector.run_sync()
        assert "p2" in db.records
        assert store.values_for("confluence_pages/ENG")["last_sync_time"] == held

        db.fail_delete_for = set()
        await connector.run_sync()
        assert "p2" not in db.records
        assert store.values_for("confluence_pages/ENG")["last_sync_time"] != held

    @staticmethod
    def _search_window(atlassian_api, search, since: str, changed: list[dict[str, Any]]) -> None:
        """The change search returns ``changed`` only to a sync asking from ``since`` (checkpoint less 24 hours)."""

        def windowed(request: httpx.Request) -> httpx.Response:
            cql = AtlassianApiStub.query(request)["cql"]
            search.cql.append(cql)
            found = changed if cql.startswith("type=page") and f'lastModified > "{since}"' in cql else []
            return json_response(listing(found))

        atlassian_api.on("GET", f"{API}/content/search", windowed)

    async def test_a_page_that_failed_to_save_is_read_again_next_sync(self, atlassian_api, db, store, search) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        held = self._hold_checkpoint(store)
        self._search_window(atlassian_api, search, "2023-12-31 00:00", [content("p1", version=2), content("p2", version=2)])
        db.fail_lookup_for = {"p2"}

        await connector.run_sync()

        stored = store.values_for("confluence_pages/ENG")
        assert db.records["p2"].external_revision_id == "1"
        assert stored["last_sync_time"] == held, "a page that failed to save holds the checkpoint"
        assert json.loads(stored["failedPages"]) == {"p2": 1}

        db.fail_lookup_for = set()
        await connector.run_sync()

        stored = store.values_for("confluence_pages/ENG")
        assert db.records["p2"].external_revision_id == "2", "the edit is read again, not lost behind the checkpoint"
        assert stored["last_sync_time"] > held and json.loads(stored["failedPages"]) == {}

    async def test_a_page_that_keeps_failing_is_given_up_on_and_the_checkpoint_moves_on(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        held = self._hold_checkpoint(store)
        self._search_window(atlassian_api, search, "2023-12-31 00:00", [content("p1", version=2), content("p2", version=2)])
        db.fail_lookup_for = {"p2"}
        logs = record_logs(connector)

        for attempt in range(1, 5):
            await connector.run_sync()
            stored = store.values_for("confluence_pages/ENG")
            assert stored["last_sync_time"] == held
            assert json.loads(stored["failedPages"]) == {"p2": attempt}

        await connector.run_sync()

        stored = store.values_for("confluence_pages/ENG")
        assert stored["last_sync_time"] > held, "one broken page does not freeze the space"
        assert json.loads(stored["failedPages"]) == {}
        assert json.loads(stored["givenUpPages"]) == {"p2": "2024-05-01T10:00:00.000Z"}
        assert any("p2" in m and "after 5 syncs" in m for m in logged(logs))

        edited = content("p2", version=3)
        edited["version"]["when"] = edited["history"]["lastUpdated"]["when"] = "2024-06-01T10:00:00.000Z"
        search.add("page", 0, listing([edited]))
        atlassian_api.on("GET", f"{API}/content/search", search)
        db.fail_lookup_for = set()
        await connector.run_sync()

        assert db.records["p2"].external_revision_id == "3", "once it changes it is tried afresh"
        assert json.loads(store.values_for("confluence_pages/ENG")["givenUpPages"]) == {}

    async def test_a_page_that_keeps_failing_when_synced_by_id_is_counted_and_given_up_on(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        del db.records["p2"]
        db.fail_lookup_for = {"p2"}

        def by_id(request: httpx.Request) -> httpx.Response:
            cql = AtlassianApiStub.query(request)["cql"]
            search.cql.append(cql)
            return json_response(listing([content("p2")] if "id in (p2)" in cql else []))

        atlassian_api.on("GET", f"{API}/content/search", by_id)
        held = self._hold_checkpoint(store)

        for attempt in range(1, 5):
            await connector.run_sync()
            stored = store.values_for("confluence_pages/ENG")
            assert json.loads(stored["failedPages"]) == {"p2": attempt}
            assert stored["last_sync_time"] == held

        await connector.run_sync()
        assert json.loads(store.values_for("confluence_pages/ENG")["givenUpPages"]) == {"p2": "2024-05-01T10:00:00.000Z"}

        search.cql.clear()
        await connector.run_sync()
        assert not [c for c in search.cql if "id in (p2)" in c], "an unchanged given-up page is not synced by id again"
        assert store.values_for("confluence_pages/ENG")["last_sync_time"] > held

    async def test_a_page_listed_without_a_title_holds_the_checkpoint_until_given_up_and_is_never_removed(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        held = self._hold_checkpoint(store)
        untitled = {**content("p2", version=2), "title": None}
        self._search_window(atlassian_api, search, "2023-12-31 00:00", [content("p1", version=2), untitled])

        for attempt in range(1, 5):
            search.cql.clear()
            await connector.run_sync()
            stored = store.values_for("confluence_pages/ENG")
            assert any('lastModified > "2023-12-31 00:00"' in c for c in search.cql), "it is read again"
            assert stored["last_sync_time"] == held, "an item that could not be saved holds the checkpoint"
            assert json.loads(stored.get("failedPages") or "{}") == {"p2": attempt}

        await connector.run_sync()

        stored = store.values_for("confluence_pages/ENG")
        assert stored["last_sync_time"] > held, "one untitled page does not freeze the space"
        assert json.loads(stored["failedPages"]) == {}
        assert json.loads(stored["givenUpPages"]) == {"p2": "2024-05-01T10:00:00.000Z"}

        search.add("page", 0, listing([untitled]))
        atlassian_api.on("GET", f"{API}/content/search", search)
        moved = stored["last_sync_time"]
        await connector.run_sync()

        stored = store.values_for("confluence_pages/ENG")
        assert stored["last_sync_time"] >= moved and json.loads(stored["failedPages"]) == {}, "given up, so not held again"
        assert db.records["p2"].record_name == "Title p2", "the stored page still exists, so it is kept as it was"

    async def test_the_stored_space_is_read_once_for_its_pages_and_blog_posts(self, atlassian_api, db, store, search) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.add("blogpost", 0, listing([content("b1", ctype="blogpost")]))
        await connector.run_sync()
        assert "b1" in db.records
        search.add("blogpost", 0, listing([]))
        search.existing["page"] = [content("p1"), child_of("p3", "p1")]
        search.existing["blogpost"] = []
        reads = count_group_reads(db)

        await connector.run_sync()

        assert len(reads) == 1, "one read of the space serves both its pages and its blog posts"
        assert not {"p2", "b1"} & set(db.records), "both types are still brought in line from it"

    async def test_a_blog_post_saved_after_the_pages_read_is_still_checked_that_sync(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        search.add("blogpost", 0, listing([content("b1", ctype="blogpost")]))
        search.existing["blogpost"] = []
        reads = count_group_reads(db)

        await connector.run_sync()

        assert "b1" not in db.records, "the search had it, the space no longer does"
        assert len(reads) == 2, "the read made for the pages predates b1, so the space is read again"

    async def test_a_narrowed_page_filter_removes_the_page_and_the_pages_below_it(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        connector.config_service.config["filters"] = {
            "sync": {"values": {"page_ids": {"operator": "not_in", "type": "list", "value": ["p2"]}}}
        }
        store.sync_points.clear()
        search.add("page", 0, listing([content("p1")]))

        await connector.run_sync()

        assert "p1" in db.records
        assert not {"p2", "a2", "c1", "p3"} & set(db.records)

    async def test_a_modified_date_filter_removes_only_what_it_clearly_leaves_out(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        cutoff = int(datetime(2024, 5, 2, tzinfo=timezone.utc).timestamp() * 1000)
        connector.config_service.config["filters"] = {
            "sync": {"values": {"modified": {"operator": "is_after", "type": "datetime", "value": {"start": cutoff}}}}
        }
        store.sync_points.clear()
        old = {**content("p1"), "version": {"number": 1, "when": "2024-03-01T10:00:00.000Z"}}
        search.existing["page"] = [old, content("p2", attachments=[attachment("a2")]), child_of("p3", "p2")]

        await connector.run_sync()

        assert "p1" not in db.records, "modified two months before the cutoff"
        assert {"p2", "p3"} <= set(db.records), "a day inside the margin: the filter's own search decides"

    async def test_a_modified_date_filter_removes_what_it_leaves_out_by_more_than_a_day(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        cutoff = int(datetime(2024, 5, 2, tzinfo=timezone.utc).timestamp() * 1000)
        connector.config_service.config["filters"] = {
            "sync": {"values": {"modified": {"operator": "is_after", "type": "datetime", "value": {"start": cutoff}}}}
        }
        store.sync_points.clear()
        older = {**content("p1"), "version": {"number": 1, "when": "2024-04-30T18:00:00.000Z"}}
        search.existing["page"] = [older, content("p2", attachments=[attachment("a2")]), child_of("p3", "p2")]

        await connector.run_sync()

        assert "p1" not in db.records, "30 hours before the cutoff, which the search applies as set"
        assert {"p2", "p3"} <= set(db.records), "14 hours before it: inside the time-zone margin"

    async def test_a_narrowed_space_filter_removes_the_space_and_a_re_added_space_is_read_in_full(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("HR", 20)], "_links": {"base": BASE}})
        await connector.run_sync()
        assert "20" in db.record_groups

        connector.config_service.config["filters"] = {
            "sync": {"values": {"space_keys": {"operator": "not_in", "type": "list", "value": ["ENG"]}}}
        }
        store.sync_points.clear()
        await connector.run_sync()

        assert "10" not in db.record_groups
        assert not {"p1", "p2", "p3"} & set(db.records)
        assert store.values_for("confluence_pages/ENG")["last_sync_time"] == ""

    async def test_a_space_filter_that_leaves_out_every_listed_space_removes_them_and_completes(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        connector.config_service.config["filters"] = {
            "sync": {"values": {"space_keys": {"operator": "not_in", "type": "list", "value": ["ENG"]}}}
        }

        await connector.run_sync()

        assert "10" not in db.record_groups
        assert not {"p1", "p2", "p3"} & set(db.records)
        assert connector.full_sync_coverage().complete is True, "the filters leaving every space out is no failed read"

    async def test_a_removed_space_leaves_no_type_docs_behind(self, atlassian_api, db, store, search) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        eng_ids = {r.id for r in db.records.values() if r.external_record_group_id == "10"}
        assert eng_ids and eng_ids <= db.type_docs
        atlassian_api.on("GET", f"{API}/space", {"results": [space("HR", 20)], "_links": {"base": BASE}})

        await connector.run_sync()

        assert "10" not in db.record_groups
        assert not eng_ids & db.type_docs, "a record's isOfType doc goes with it"

    async def test_a_space_with_no_records_left_is_removed_when_it_leaves(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("HR", 20)], "_links": {"base": BASE}})
        await connector.run_sync()
        assert "20" in db.record_groups
        assert not any(r.external_record_group_id == "20" for r in db.records.values())

        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10)], "_links": {"base": BASE}})
        await connector.run_sync()

        assert "20" not in db.record_groups
        assert "10" in db.record_groups

    async def test_a_failed_read_of_the_stored_spaces_removes_none_and_the_next_sync_does(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("HR", 20)], "_links": {"base": BASE}})
        await connector.run_sync()
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10)], "_links": {"base": BASE}})
        db.fail_group_read = True

        await connector.run_sync()
        assert "20" in db.record_groups

        db.fail_group_read = False
        await connector.run_sync()
        assert "20" not in db.record_groups

    async def test_a_new_space_whose_permissions_cannot_be_read_does_not_stop_a_removal(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("HR", 20)], "_links": {"base": BASE}})
        await connector.run_sync()
        # OPS is new and its grants can't be read, so it is listed but not saved; HR has left.
        atlassian_api.on("GET", f"{API}/space/OPS/permissions", json_response({"message": "busy"}, status=503))
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("OPS", 30)], "_links": {"base": BASE}})

        await connector.run_sync()

        assert "30" not in db.record_groups
        assert "20" not in db.record_groups
        assert "10" in db.record_groups

    async def test_a_space_removed_in_several_deletes_does_not_trip_over_records_already_taken(
        self, atlassian_api, db, store, search, monkeypatch
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        monkeypatch.setattr(confluence_dc_removal, "RECORD_DELETE_CHUNK", 1)
        scan = db.get_records_by_status

        async def pages_first(*args: object, **kwargs: object) -> list[Record]:
            # A page ahead of its files and comments, which its cascade takes with it.
            return sorted(await scan(*args, **kwargs), key=lambda r: r.record_type != RecordType.CONFLUENCE_PAGE)

        monkeypatch.setattr(db, "get_records_by_status", pages_first)
        atlassian_api.on("GET", f"{API}/space", {"results": [space("HR", 20)], "_links": {"base": BASE}})

        await connector.run_sync()

        assert "10" not in db.record_groups
        assert not any(r.external_record_group_id == "10" for r in db.records.values())

    async def test_a_listed_space_that_fails_to_process_keeps_its_records(
        self, atlassian_api, db, store, search, monkeypatch
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("HR", 20)], "_links": {"base": BASE}})
        real_transform = connector._transform_to_space_record_group

        def fails_for_eng(space_data: dict[str, Any], base_url: str | None = None) -> object:
            if space_data.get("key") == "ENG":
                raise RuntimeError("could not build the space")
            return real_transform(space_data, base_url)

        monkeypatch.setattr(connector, "_transform_to_space_record_group", fails_for_eng)
        store.sync_points.clear()

        await connector.run_sync()

        assert "10" in db.record_groups
        assert {"p1", "p2", "p3"} <= set(db.records), "a space Confluence listed is not treated as gone"

    async def test_a_listed_space_without_an_id_keeps_the_stored_spaces(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        eng_without_id = {k: v for k, v in space("ENG", 10).items() if k != "id"}
        atlassian_api.on("GET", f"{API}/space", {"results": [eng_without_id, space("HR", 20)], "_links": {"base": BASE}})
        store.sync_points.clear()

        await connector.run_sync()

        assert "10" in db.record_groups
        assert {"p1", "p2", "p3"} <= set(db.records), "a space listed without its id is not treated as gone"

    @pytest.mark.parametrize("answer", [
        pytest.param(json_response({"message": "busy"}, status=503), id="failed"),
        pytest.param({"results": [], "_links": {"base": BASE}}, id="empty"),
    ])
    async def test_a_space_listing_that_is_empty_or_failed_removes_no_space(
        self, atlassian_api, db, store, search, answer
    ) -> None:
        connector = await self._two_pages_synced(atlassian_api, db, store, search)
        atlassian_api.on("GET", f"{API}/space", answer)

        await connector.run_sync()

        assert "10" in db.record_groups
        assert {"p1", "p2"} <= set(db.records)
        assert connector.full_sync_coverage().complete is False


ANA = user("ana", "ana@example.com")
BOB = user("bob", "bob@example.com")
P1_RESTRICTION = f"{API}/content/p1/restriction/relevantViewRestrictions"


class TestAFullSyncUnlinksOnlyWhatLeftConfluence:
    """A full sync takes the links of every stored node it did not write. What is still in
    Confluence keeps them, whether the sync read it, kept it as stored, or could not read it."""

    @staticmethod
    async def _full_sync(connector, db, store) -> dict[str, set[str]]:
        store.sync_points.clear()  # the full sync's prep deletes the checkpoints
        db.kept_records.clear()
        graph = await run_full_sync(connector, db)
        return unlinked_by_full_sync(graph, db)

    @staticmethod
    def _among(unlinked: dict[str, set[str]], ids: set[str]) -> set[str]:
        return set().union(*unlinked.values()) & ids

    async def test_a_page_comment_and_group_gone_from_confluence_are_unlinked_and_nothing_else(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        comments = f"{API}/content/p1/child/comment"
        atlassian_api.on("GET", comments, {"results": [comment("c1"), comment("c2")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")]), content("p2")]))
        await connector.run_sync()
        assert {"p1", "a1", "c1", "c2", "p2"} <= set(db.records)
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        atlassian_api.on("GET", comments, {"results": [comment("c1")], "_links": {"base": BASE}})

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"p1", "a1", "c1", "p2", "10", "eng"}) == set()
        assert self._among(unlinked, {"c2", "old"}) == {"c2", "old"}, "what left Confluence loses its links"

    async def test_an_archived_page_keeps_its_links_with_its_files_and_comments(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")]), content("p2")]))
        await connector.run_sync()
        assert {"p1", "a1", "c1"} <= set(db.records)
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        search.add("page", 0, listing([content("p2")]))  # the search leaves archived pages out
        search.existing["page"] = [content("p2")]
        search.archived = [content("p1")]

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"p1", "a1", "c1"}) == set(), "an archived page is still in Confluence"
        assert self._among(unlinked, {"old"}) == {"old"}

    async def test_an_archived_page_keeps_its_replies_and_comment_files(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/comment", {"results": [comment("r1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/attachment", {"results": [attachment("ca1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1"), content("p2")]))
        await connector.run_sync()
        assert {"p1", "c1", "r1", "ca1"} <= set(db.records)
        search.add("page", 0, listing([content("p2")]))
        search.existing["page"] = [content("p2")]
        search.archived = [content("p1")]

        unlinked = await self._full_sync(connector, db, store)

        kept = {r.external_record_id for r in db.records.values() if r.id in db.kept_records}
        assert {"p1", "c1", "r1", "ca1"} <= kept
        assert self._among(unlinked, {"p1", "c1", "r1", "ca1"}) == set()
        assert connector.full_sync_coverage().complete is True

    async def test_a_deleted_child_page_of_an_archived_page_is_still_removed(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1"), child_of("p3", "p1")]))
        await connector.run_sync()
        p3 = db.records["p3"].id
        search.add("page", 0, listing([]))
        search.existing["page"] = []
        search.archived = [content("p1")]

        await self._full_sync(connector, db, store)

        assert "p3" not in db.records, "gone from both listings"
        assert p3 not in db.kept_records
        assert connector.full_sync_coverage().complete is True

    async def test_an_incremental_run_does_not_walk_archived_pages(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")]), content("p2")]))
        await connector.run_sync()
        search.add("page", 0, listing([]))
        search.existing["page"] = [content("p2")]
        search.archived = [content("p1")]
        parents: list[str] = []
        read_children = db.get_records_by_parent

        async def spy(connector_id: str, parent_external_record_id: str, *args: object, **kwargs: object) -> list[Record]:
            parents.append(parent_external_record_id)
            return await read_children(connector_id, parent_external_record_id, *args, **kwargs)

        db.get_records_by_parent = spy

        await connector.run_sync()

        assert {"p1", "a1"} <= set(db.records)
        assert db.kept_records == []
        assert "p1" not in parents

    @staticmethod
    async def _archived_restricted_page(atlassian_api, db, store, search, *users: str) -> ConfluenceDataCenterConnector:
        """p1 (file a1, comment c1) restricted to ``users``, then archived; p2 stays current."""
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA, BOB], {})
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", P1_RESTRICTION, restricted_to(users=[{"type": "known", "userKey": u} for u in users]))
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")]), content("p2")]))
        await connector.run_sync()
        assert len(db.record_permissions["p1"]) == len(users)
        search.add("page", 0, listing([content("p2")]))
        search.existing["page"] = [content("p2")]
        search.archived = [content("p1")]
        return connector

    @pytest.mark.parametrize("now", [["ana"], ["ana", "bob"]], ids=["narrowed", "unchanged"])
    async def test_an_archived_page_gets_its_current_restrictions_with_its_files_and_comments(
        self, atlassian_api, db, store, search, now
    ) -> None:
        connector = await self._archived_restricted_page(atlassian_api, db, store, search, "ana", "bob")
        atlassian_api.on("GET", P1_RESTRICTION, restricted_to(users=[{"type": "known", "userKey": u} for u in now]))
        db.permission_updates.clear()

        unlinked = await self._full_sync(connector, db, store)

        assert {p.email for p in db.record_permissions["p1"]} == {f"{u}@example.com" for u in now}
        assert db.records["p1"].access_rule == AccessRule.RESTRICTED
        updated = {r.external_record_id: (r.inherit_permissions, perms) for r, perms in db.permission_updates}
        assert updated == {"a1": (True, []), "c1": (True, [])}, "its file and comment take the page's access"
        assert self._among(unlinked, {"p1", "a1", "c1"}) == set()
        assert connector.full_sync_coverage().complete is True
        assert len(atlassian_api.calls("GET", P1_RESTRICTION)) == 2, "one read on its first sync, one on the full sync"

    @pytest.mark.parametrize("failure", ["restriction-read", "record-lookup", "record-gone"])
    async def test_an_archived_page_whose_restrictions_cannot_be_read_unlinks_nothing(
        self, atlassian_api, db, store, search, failure
    ) -> None:
        connector = await self._archived_restricted_page(atlassian_api, db, store, search, "ana", "bob")
        if failure == "restriction-read":
            atlassian_api.on("GET", P1_RESTRICTION, json_response({"message": "busy"}, status=503))
        else:
            atlassian_api.on("GET", P1_RESTRICTION, restricted_to(users=[{"type": "known", "userKey": "ana"}]))
            db.get_typed_record_by_external_id = (
                AsyncMock(side_effect=ConnectionError("database unavailable"))
                if failure == "record-lookup"
                # Deleted between the stored scan and the refresh: its new access is not written.
                else AsyncMock(return_value=None)
            )
        p1, c1 = db.records["p1"].id, db.records["c1"].id

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is False, "a full sync is still owed"
        assert unlinked == {"records": set(), "record_groups": set(), "groups": set()}
        assert {p.email for p in db.record_permissions["p1"]} == {"ana@example.com", "bob@example.com"}
        assert db.records["p1"].access_rule == AccessRule.RESTRICTED
        assert {p1, c1} <= set(db.kept_records)

    async def test_a_stand_in_group_named_only_by_an_archived_page_keeps_its_links(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._archived_restricted_page(atlassian_api, db, store, search, "contractor")

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"contractor", "p1"}) == set()
        assert connector.full_sync_coverage().complete is True

    async def test_a_run_that_is_not_a_full_sync_reads_no_archived_page_restrictions(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await self._archived_restricted_page(atlassian_api, db, store, search, "ana", "bob")
        reads = len(atlassian_api.calls("GET", P1_RESTRICTION))
        store.sync_points.clear()  # read in full, as a space added to the filters is

        await connector.run_sync()

        assert len(atlassian_api.calls("GET", P1_RESTRICTION)) == reads
        assert {p.email for p in db.record_permissions["p1"]} == {"ana@example.com", "bob@example.com"}

    async def test_a_reused_stand_in_group_keeps_its_links(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        search.add("page", 0, listing([content("p1"), content("p2")]))
        p1, p2 = (f"{API}/content/{p}/restriction/relevantViewRestrictions" for p in ("p1", "p2"))
        atlassian_api.on("GET", p1, restricted_to(users=[{"type": "known", "userKey": "contractor"}]))
        atlassian_api.on("GET", p2, restricted_to(users=[{"type": "known", "userKey": "ghost"}]))
        await connector.run_sync()
        assert {"contractor", "ghost"} <= {g.source_user_group_id for g, _ in db.user_groups}
        atlassian_api.on("GET", p2, restricted_to(users=[{"type": "known", "userKey": "ana"}]))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"contractor", "p1"}) == set(), "p1 still names the user without an email"
        assert self._among(unlinked, {"ghost"}) == {"ghost"}, "a stand-in no restriction names any more goes"
        assert connector.full_sync_coverage().complete is True

    @pytest.mark.parametrize("failure", ["member-listing", "member-lookup"])
    async def test_a_group_whose_members_could_not_be_read_keeps_its_links(
        self, atlassian_api, db, store, search, failure
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        atlassian_api.on(
            "GET", f"{API}/space/ENG/permissions",
            json_response([{"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "group", "name": "eng"}}]),
        )
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        if failure == "member-listing":
            atlassian_api.on("GET", f"{API}/group/eng/member", json_response({"message": "busy"}, status=503))
        else:
            db.get_all_app_users = AsyncMock(side_effect=ConnectionError("database unavailable"))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"eng"}) == set(), "its stored members and its space grant stay"
        assert "ana@example.com" in db.members_of("eng")
        assert (EntityType.GROUP, "eng", PermissionType.READ) in {
            (p.entity_type, p.external_id, p.type) for p in db.record_group_permissions["10"]
        }
        assert self._among(unlinked, {"old"}) == {"old"}
        assert connector.full_sync_coverage().complete is True

    async def test_a_group_listing_cut_short_unlinks_no_group(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        first = {"results": [{"name": f"g{i}"} for i in range(50)]}
        pages = {"0": first, "50": {"results": [{"name": "late"}, {"name": "old"}]}}
        groups = f"{API}/group"
        atlassian_api.on("GET", groups, lambda r: json_response(pages[AtlassianApiStub.query(r).get("start", "0")]))
        await connector.run_sync()
        assert {"late", "old"} <= {g.source_user_group_id for g, _ in db.user_groups}
        pages["50"] = json_response({"message": "busy"}, status=503)
        atlassian_api.on(
            "GET", groups,
            lambda r: pages["50"] if AtlassianApiStub.query(r).get("start") == "50" else json_response(first),
        )

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"late", "old"}) == set(), "a group the listing did not reach may still be there"
        assert connector.full_sync_coverage().complete is False

    async def test_a_space_listing_cut_short_unlinks_no_space(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        spaces = {
            "0": json_response({"results": [space("ENG", 10)], "_links": {"base": BASE, "next": f"{API}/space?limit=25&start=25"}}),
            "25": json_response({"results": [space("HR", 20)], "_links": {"base": BASE}}),
        }
        atlassian_api.on("GET", f"{API}/space", lambda r: spaces[AtlassianApiStub.query(r).get("start", "0")])
        hr_page = {**content("h1", space_id=20), "space": {"id": 20, "key": "HR"}}

        def by_space(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
            def answer(request: httpx.Request) -> httpx.Response:
                q = AtlassianApiStub.query(request)
                if q.get("spaceKey") == "HR" or "space='HR'" in q.get("cql", ""):
                    return json_response(listing([hr_page] if q.get("status") != "archived" else []))
                return handler(request)
            return answer

        atlassian_api.on("GET", f"{API}/content/search", by_space(search))
        atlassian_api.on("GET", f"{API}/content", by_space(search.database))
        await connector.run_sync()
        assert "20" in db.record_groups and "h1" in db.records
        spaces["25"] = json_response({"message": "busy"}, status=503)

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"20", "h1"}) == set(), "a space the listing did not reach may still be there"
        assert connector.full_sync_coverage().complete is False

    async def test_a_space_whose_grants_could_not_be_read_keeps_its_links(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("EMPTY", 30)], "_links": {"base": BASE}})
        grants = f"{API}/space/EMPTY/permissions"
        atlassian_api.on(
            "GET", grants,
            json_response([{"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "group", "name": "eng"}}]),
        )
        await connector.run_sync()
        assert db.record_group_permissions["30"]
        atlassian_api.on("GET", grants, json_response({"message": "busy"}, status=503))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"30"}) == set(), "a space with no pages whose grants were kept stays linked"
        assert connector.full_sync_coverage().complete is True

    async def test_a_space_whose_grants_could_not_be_read_or_kept_unlinks_nothing(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        atlassian_api.on("GET", f"{API}/space", {"results": [space("ENG", 10), space("EMPTY", 30)], "_links": {"base": BASE}})
        grants = f"{API}/space/EMPTY/permissions"
        atlassian_api.on(
            "GET", grants,
            json_response([{"operation": {"operationKey": "read", "targetType": "space"}, "subject": {"type": "group", "name": "eng"}}]),
        )
        await connector.run_sync()
        atlassian_api.on("GET", grants, json_response({"message": "busy"}, status=503))
        db.note_kept_record_group = AsyncMock(side_effect=ConnectionError("database unavailable"))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"30"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_a_group_that_cannot_be_noted_kept_unlinks_no_group(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        atlassian_api.on("GET", f"{API}/group/eng/member", json_response({"message": "busy"}, status=503))
        db.fail_note_kept_group = True

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"eng", "old"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_a_group_that_fails_to_save_unlinks_no_group(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        save = db.on_new_user_groups

        async def failing_for_eng(groups: list[tuple[Any, list[Any]]]) -> None:
            if any(g.name == "eng" for g, _ in groups):
                raise ConnectionError("database unavailable")
            await save(groups)

        db.on_new_user_groups = failing_for_eng

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"eng", "old"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_a_group_deleted_before_its_members_are_read_is_saved_empty(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        await connector.run_sync()
        atlassian_api.on("GET", f"{API}/group/eng/member", json_response({"message": "no group"}, status=404))
        db.note_kept_user_group = AsyncMock()

        unlinked = await self._full_sync(connector, db, store)

        assert db.members_of("eng") == []
        assert self._among(unlinked, {"eng"}) == set(), "written by this sync"
        db.note_kept_user_group.assert_not_awaited()
        assert connector.full_sync_coverage().complete is True

    async def test_a_stand_in_group_is_noted_kept_only_when_reused(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")]))
        atlassian_api.on(
            "GET", f"{API}/content/p1/restriction/relevantViewRestrictions",
            restricted_to(users=[{"type": "known", "userKey": "contractor"}]),
        )
        lookups: list[Optional[AppUserGroup]] = []
        notes: list[list[Any]] = []
        lookup, note = db.get_user_group_by_external_id, db.note_kept_user_groups

        async def spy_lookup(connector_id: str, external_id: str) -> object:
            found = await lookup(connector_id, external_id)
            lookups.append(found)
            return found

        def spy_note(connector_id: str, groups: list[Any]) -> None:
            notes.append(groups)
            note(connector_id, groups)

        db.get_user_group_by_external_id, db.note_kept_user_groups = spy_lookup, spy_note

        await connector.run_sync()
        assert lookups[0] is None, "the first sync creates the stand-in"
        assert notes == [[g] for g in lookups if g is not None]

        lookups.clear()
        notes.clear()
        await self._full_sync(connector, db, store)

        assert lookups and all(g is not None for g in lookups)
        assert notes == [[g] for g in lookups]

    async def test_a_page_whose_restrictions_could_not_be_read_keeps_its_links(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")]), content("p2")]))
        await connector.run_sync()
        assert {"p1", "a1", "c1"} <= set(db.records)
        atlassian_api.on(
            "GET", f"{API}/content/p1/restriction/relevantViewRestrictions", json_response({"message": "busy"}, status=503)
        )

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"p1", "a1", "c1"}) == set()

    async def test_a_page_that_failed_to_save_keeps_its_links(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")]), content("p2")]))
        await connector.run_sync()
        db.fail_lookup_for = {"p1"}

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"p1", "a1"}) == set()

    async def test_a_page_listing_cut_short_unlinks_no_page(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        search.add("page", 0, listing([content("p1")], next_start=50))
        search.add("page", 50, listing([content("p2")]))
        await connector.run_sync()
        assert {"p1", "p2"} <= set(db.records)
        search.add("page", 50, json_response({"message": "busy"}, status=503))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"p2"}) == set()

    async def test_attachments_whose_listing_failed_keep_their_links(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        truncated = content("p1", attachments=[attachment("a1")])
        truncated["children"]["attachment"]["size"] = 2
        search.add("page", 0, listing([truncated]))
        files = f"{API}/content/p1/child/attachment"
        atlassian_api.on("GET", files, {"results": [attachment("a1"), attachment("a2")], "_links": {"base": BASE}})
        await connector.run_sync()
        assert {"a1", "a2"} <= set(db.records)
        atlassian_api.on("GET", files, json_response({"message": "busy"}, status=503))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"a1", "a2"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_attachments_whose_listing_raised_keep_their_links(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        truncated = content("p1", attachments=[attachment("a1")])
        truncated["children"]["attachment"]["size"] = 2
        search.add("page", 0, listing([truncated]))
        files = f"{API}/content/p1/child/attachment"
        atlassian_api.on("GET", files, {"results": [attachment("a1"), attachment("a2")], "_links": {"base": BASE}})
        await connector.run_sync()

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection reset", request=request)

        atlassian_api.on("GET", files, unreachable)

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"a1", "a2"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_comments_whose_listing_failed_keep_their_links(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        comments = f"{API}/content/p1/child/comment"
        atlassian_api.on("GET", comments, {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/comment", {"results": [comment("r1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        assert {"c1", "r1"} <= set(db.records)
        atlassian_api.on("GET", comments, json_response({"message": "busy"}, status=503))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"c1", "r1"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_replies_whose_listing_failed_keep_their_links(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        replies = f"{API}/content/c1/child/comment"
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", replies, {"results": [comment("r1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        assert {"c1", "r1"} <= set(db.records)
        atlassian_api.on("GET", replies, json_response({"message": "busy"}, status=503))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"c1", "r1"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_a_comment_without_an_author_keeps_the_run_complete(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        authorless = {**comment("cx"), "version": {"number": 1, "when": "2024-05-02T10:00:00.000Z"}}
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1"), authorless], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})

        unlinked = await self._full_sync(connector, db, store)

        assert "cx" not in db.records
        assert connector.full_sync_coverage().complete is True, "a comment skipped for its shape is skipped every run"
        assert self._among(unlinked, {"p1", "c1", "old"}) == {"old"}

    @staticmethod
    def _space_with_homepage(atlassian_api) -> None:
        def spaces(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("expand") == "homepage":
                return json_response({"results": [{**space("ENG", 10), "homepage": {"id": 500, "title": "Home"}}]})
            return json_response({"results": [space("ENG", 10)], "_links": {"base": BASE}})

        atlassian_api.on("GET", f"{API}/space", spaces)

    @pytest.mark.parametrize("failure", ["error-status", "unparsable"])
    async def test_a_stored_homepage_whose_backfill_failed_keeps_its_links(
        self, atlassian_api, db, store, search, failure
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        self._space_with_homepage(atlassian_api)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        search.add("page", 0, listing([content("p1")]))
        search.existing["page"] = [content("p1"), content("500")]
        await connector.run_sync()
        assert "500" in db.records
        atlassian_api.on("GET", f"{API}/content/500", unparsable_backfill if failure == "unparsable" else json_response({"message": "busy"}, status=503))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"500", "p1"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_a_stored_homepage_whose_lookup_fails_after_a_failed_backfill_keeps_its_links(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        self._space_with_homepage(atlassian_api)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        search.add("page", 0, listing([content("p1")]))
        search.existing["page"] = [content("p1"), content("500")]
        await connector.run_sync()
        assert "500" in db.records
        atlassian_api.on("GET", f"{API}/content/500", json_response({"message": "busy"}, status=503))
        read_stored = db.get_record_by_external_id

        async def unavailable_for_homepage(
            connector_id: str, external_record_id: str, *, raise_on_error: bool = False
        ) -> Record | None:
            # As the real stores: a failed read raises only when asked to, and otherwise reads as never stored.
            if external_record_id == "500":
                if raise_on_error:
                    raise RuntimeError("database unavailable")
                return None
            return await read_stored(connector_id, external_record_id, raise_on_error=raise_on_error)

        db.get_record_by_external_id = unavailable_for_homepage

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"500"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_an_archived_walk_ends_on_a_cycle_and_notes_each_record_once(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        atlassian_api.on("GET", f"{API}/content/p1/child/comment", {"results": [comment("c1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1"), content("p2")]))
        await connector.run_sync()
        search.add("page", 0, listing([content("p2")]))
        search.existing["page"] = [content("p2")]
        search.archived = [content("p1")]
        read_children = db.get_records_by_parent
        p1 = db.records["p1"]
        loop_back = p1.model_copy(update={"id": "loop-back", "record_type": RecordType.COMMENT})

        async def cyclic(connector_id: str, parent_external_record_id: str, *args: object, **kwargs: object) -> list[Record]:
            children = await read_children(connector_id, parent_external_record_id, *args, **kwargs)
            return [*children, loop_back] if parent_external_record_id == "c1" else children

        db.get_records_by_parent = cyclic

        await asyncio.wait_for(self._full_sync(connector, db, store), timeout=10)

        assert sorted(db.kept_records) == sorted([p1.id, db.records["c1"].id, "loop-back"])

    async def test_a_homepage_never_stored_and_never_readable_keeps_the_run_complete(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        self._space_with_homepage(atlassian_api)
        atlassian_api.on("GET", f"{API}/content/500", json_response({"message": "forbidden"}, status=403))
        search.add("page", 0, listing([content("p1")]))
        search.existing["page"] = [content("p1"), content("500")]
        await connector.run_sync()
        assert "500" not in db.records
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is True, "nothing stored was left unwritten"
        assert self._among(unlinked, {"p1", "old"}) == {"old"}

    async def test_a_space_listing_that_comes_back_empty_unlinks_no_space(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        atlassian_api.on("GET", f"{API}/space", {"results": [], "_links": {"base": BASE}})

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"10", "p1", "old"}) == set(), "an empty listing is the account failing"
        assert connector.full_sync_coverage().complete is False

    async def test_a_database_listing_that_cannot_be_read_keeps_the_space_linked(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        search.add("page", 0, listing([content("p1"), content("p2")]))
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        search.existing["page"] = json_response({"message": "busy"}, status=503)

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"p1", "p2", "10", "old"}) == set()
        assert connector.full_sync_coverage().complete is False

    async def test_a_new_page_that_fails_to_sync_by_id_keeps_the_run_complete(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        search.existing["page"] = [content("p1"), content("p9")]

        def by_id(request: httpx.Request) -> httpx.Response:
            if "id in (p9)" in AtlassianApiStub.query(request)["cql"]:
                return json_response(listing([content("p9")]))
            return search(request)

        atlassian_api.on("GET", f"{API}/content/search", by_id)
        db.fail_lookup_for = {"p9"}

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is True, "p9 was never stored, so nothing is left unwritten"
        assert self._among(unlinked, {"p1", "old"}) == {"old"}

    @pytest.mark.parametrize("failure", ["no-title", "save-raises"])
    async def test_a_never_stored_page_that_always_fails_keeps_full_syncs_complete(
        self, atlassian_api, db, store, search, failure
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        search.add("page", 0, listing([content("p2")]))
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        if failure == "no-title":
            search.add("page", 0, listing([{**content("p3"), "title": ""}, content("p2")]))
        else:
            search.add("page", 0, listing([content("p9"), content("p2")]))
            db.fail_lookup_for = {"p9"}

        for _ in range(3):
            unlinked = await self._full_sync(connector, db, store)
            assert connector.full_sync_coverage().complete is True, "a page never stored leaves nothing unwritten"
        assert self._among(unlinked, {"p2", "old"}) == {"old"}

    async def test_each_full_sync_reports_its_own_coverage(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        search.add("page", 0, listing([content("p1")]))
        await connector.run_sync()
        search.existing["page"] = json_response({"message": "busy"}, status=503)

        await self._full_sync(connector, db, store)
        assert connector.full_sync_coverage().complete is False

        del search.existing["page"]
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is True
        assert self._among(unlinked, {"p1", "eng", "old"}) == {"old"}

    async def test_an_archived_page_the_filters_now_leave_out_is_removed_not_kept(
        self, atlassian_api, db, store, search, monkeypatch
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")]), content("p2")]))
        await connector.run_sync()
        p1 = db.records["p1"].id
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        search.add("page", 0, listing([content("p2")]))
        search.existing["page"] = [content("p2")]
        search.archived = [content("p1")]
        filtered_out = connector._listed_item_filtered_out
        monkeypatch.setattr(
            connector, "_listed_item_filtered_out", lambda item_id, *args: item_id == "p1" or filtered_out(item_id, *args)
        )

        unlinked = await self._full_sync(connector, db, store)

        assert "p1" not in db.records
        assert p1 not in db.kept_records, "a page being removed is not also kept"
        assert connector.full_sync_coverage().complete is True
        assert self._among(unlinked, {"p2", "old"}) == {"old"}

    async def test_a_group_listed_with_an_id_is_kept_by_that_id(self, atlassian_api, db, store, search) -> None:
        connector = await make_connector(atlassian_api, db, store)
        groups = f"{API}/group"
        atlassian_api.on("GET", f"{API}/user/list", {"results": [ANA]})
        atlassian_api.on("GET", groups, {"results": [
            {"type": "group", "name": "eng", "id": "gid-eng"}, {"type": "group", "name": "old", "id": "gid-old"},
        ]})
        for name in ("eng", "old"):
            atlassian_api.on("GET", f"{API}/group/{name}/member", {"results": [ANA]})
        await connector.run_sync()
        assert {"gid-eng", "gid-old"} <= {g.source_user_group_id for g, _ in db.user_groups}
        atlassian_api.on("GET", groups, {"results": [{"type": "group", "name": "eng", "id": "gid-eng"}]})
        atlassian_api.on("GET", f"{API}/group/eng/member", json_response({"message": "busy"}, status=503))

        unlinked = await self._full_sync(connector, db, store)

        assert self._among(unlinked, {"gid-eng", "gid-old"}) == {"gid-old"}
        assert connector.full_sync_coverage().complete is True

    async def test_stored_records_that_cannot_be_read_unlink_nothing(
        self, atlassian_api, db, store, search, monkeypatch
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        search.add("page", 0, listing([content("p1"), content("p2")]))
        await connector.run_sync()
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})
        monkeypatch.setattr(connector, "_stored_content", AsyncMock(side_effect=ConnectionError("database unavailable")))

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is False
        assert self._among(unlinked, {"p1", "p2", "10", "old"}) == set()

    @pytest.mark.parametrize("failure", ["error-status", "raises"])
    async def test_a_stored_homepage_whose_space_cannot_be_read_keeps_its_links(
        self, atlassian_api, db, store, search, failure
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        self._space_with_homepage(atlassian_api)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        search.add("page", 0, listing([content("p1")]))
        search.existing["page"] = [content("p1"), content("500")]
        await connector.run_sync()
        assert "500" in db.records

        def homepage_unreadable(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("expand") != "homepage":
                return json_response({"results": [space("ENG", 10)], "_links": {"base": BASE}})
            if failure == "raises":
                raise httpx.ConnectError("connection reset", request=request)
            return json_response({"message": "busy"}, status=503)

        atlassian_api.on("GET", f"{API}/space", homepage_unreadable)

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is False
        assert self._among(unlinked, {"500", "p1"}) == set()

    async def test_a_stored_homepage_whose_restrictions_could_not_be_read_keeps_its_links(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        self._space_with_homepage(atlassian_api)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        search.add("page", 0, listing([content("p1")]))
        search.existing["page"] = [content("p1"), content("500")]
        await connector.run_sync()
        assert "500" in db.records
        atlassian_api.on(
            "GET", f"{API}/content/500/restriction/relevantViewRestrictions", json_response({"message": "busy"}, status=503)
        )

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is False
        assert self._among(unlinked, {"500", "p1"}) == set()

    async def test_a_homepage_never_stored_whose_restrictions_cannot_be_read_keeps_the_run_complete(
        self, atlassian_api, db, store, search
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        self._space_with_homepage(atlassian_api)
        atlassian_api.on("GET", f"{API}/content/500", content("500"))
        atlassian_api.on(
            "GET", f"{API}/content/500/restriction/relevantViewRestrictions", json_response({"message": "busy"}, status=503)
        )
        search.add("page", 0, listing([content("p1")]))
        search.existing["page"] = [content("p1"), content("500")]
        await connector.run_sync()
        assert "500" not in db.records
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is True, "nothing stored was left unwritten"
        assert self._among(unlinked, {"p1", "old"}) == {"old"}

    @pytest.mark.parametrize("failure", [
        "page-file-fails", "comment-fails", "comment-file-lookup-fails", "reply-fails",
        "comments-raise", "replies-raise", "comments-bad-cursor", "replies-bad-cursor",
    ])
    async def test_a_file_comment_or_reply_left_unread_unlinks_nothing(
        self, atlassian_api, db, store, search, failure
    ) -> None:
        connector = await make_connector(atlassian_api, db, store)
        with_directory(atlassian_api, [ANA], {"eng": [ANA], "old": [ANA]})
        comments, replies = f"{API}/content/p1/child/comment", f"{API}/content/c1/child/comment"
        atlassian_api.on("GET", comments, {"results": [comment("c1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", replies, {"results": [comment("r1")], "_links": {"base": BASE}})
        atlassian_api.on("GET", f"{API}/content/c1/child/attachment", {"results": [attachment("ca1")], "_links": {"base": BASE}})
        search.add("page", 0, listing([content("p1", attachments=[attachment("a1")])]))
        await connector.run_sync()
        assert {"p1", "a1", "c1", "r1", "ca1"} <= set(db.records)
        with_directory(atlassian_api, [ANA], {"eng": [ANA]})

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection reset", request=request)

        listings = {"comments": (comments, "c1"), "replies": (replies, "r1")}
        lookups = {"page-file-fails": "a1", "comment-fails": "c1", "comment-file-lookup-fails": "ca1", "reply-fails": "r1"}
        listing_of, _, how = failure.partition("-")
        if failure in lookups:
            db.fail_lookup_for = {lookups[failure]}
        elif how == "raise":
            atlassian_api.on("GET", listings[listing_of][0], unreachable)
        else:
            path, cid = listings[listing_of]
            atlassian_api.on("GET", path, {"results": [comment(cid)], "_links": {"base": BASE, "next": f"{path}?cursor=abc"}})

        unlinked = await self._full_sync(connector, db, store)

        assert connector.full_sync_coverage().complete is False
        assert self._among(unlinked, {"p1", "a1", "c1", "r1", "ca1", "old"}) == set()

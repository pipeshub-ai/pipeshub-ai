"""One rule, for every connector: what a user listing proves about the user who authenticated it.

A connector links that user (the authenticator) to the source account it signs in as only when its
own user listing proves them ABSENT from the source. Unknown is never absence: a cut or malformed
page, an empty page that points further, a hidden or masked email, a row that could not be read, a
listing of active users only and a failed lookup all leave them UNKNOWN. Whoever a listing did name is
PRESENT, however the rest of it went.

Each ``Source`` below runs one connector's own user sync against a scripted source, and every test is
about what the ``SourceUserListing`` that sync built then says. A new connector that registers its
authenticator gets a ``Source`` here; ``TestNoConnectorSkipsTheContract`` fails until it has one, or
until it is in ``NOT_COVERED`` and named, with the test file that covers it, in this docstring.

Connectors not covered: none.

Scenarios a ``Source`` leaves out, because the source cannot express them or because they are driven
elsewhere:

- Gmail, Drive, Zoom, Slack, Confluence and ServiceNow: no page can promise more without a cursor.
- Jira Cloud and Confluence Cloud page by offset: no page carries a cursor.
- GitLab and GitHub Teams: their SDKs read every page of a member listing in one call, so a malformed
  or cut page arrives as a failed call. A "page" here is one group or one organization. GitHub's
  outside collaborators and verified-domain outages: sources/test_authenticated_identity_hooks.py.
- Zoom lists active users only, and Notion, GitHub and Confluence Data Center rows carry no status: no
  listed-but-inactive creator.
- The Jira Data Center releases without /user/list:
  sources/atlassian/test_jira_authenticator_listing_fallback.py.
- A GitLab walk over configured projects or with exclusions: sources/test_gitlab/test_authenticator_link.py.
- When a sync reads its listing and hands it over: sources/test_authenticated_identity_hooks.py and the
  connectors' own authenticator link tests.
"""

import ast
import re
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.source_user_listing import (
    Presence,
    SourceUserListing,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
)
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.github_teams import users as github_users
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab.users import UsersSync as GitLabUsersSync
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.connectors.sources.google.drive.team.user_status import UserStatusTracker
from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector
from app.connectors.sources.linear.connector import LinearConnector
from app.connectors.sources.notion.connector import NotionConnector
from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.servicenow.servicenow.constants import ServiceNowDefaults
from app.connectors.sources.slack.team.connector import SlackConnector
from app.connectors.sources.zoom.connector import ZoomConnector
from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.salesforce.salesforce import SalesforceResponse
from app.sources.external.servicenow.models import TableAPIRecord, TableAPIResponse

# Whole modules: importing their test classes by name would collect those tests again here.
from tests.unit.connectors.sources import test_authenticated_identity_hooks as hooks
from tests.unit.connectors.sources import (
    test_notion_connector_sync_reconcile as notion_fakes,
)
from tests.unit.connectors.sources import test_salesforce_connector as salesforce_fakes
from tests.unit.connectors.sources.atlassian import (
    test_jira_authenticator_listing_fallback as jira_fakes,
)
from tests.unit.connectors.sources.linear import (
    test_linear_connector_behaviour as linear_fakes,
)
from tests.unit.connectors.sources.servicenow import servicenow_fakes
from tests.unit.connectors.sources.test_gitlab import conftest as gitlab_fakes

CREATOR = "creator@corp.com"
LISTED_CREATOR = "Creator@Corp.com"  # as the source spells it
OTHER = "other@corp.com"
THIRD = "third@corp.com"
MASKED = "creator at corp dot com"


# ---------------------------------------------------------------------------
# What a source answers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    """A listed user. ``email`` None or "": the source does not show it. ``also``: the other addresses
    it shows for the same user."""

    email: str | None
    active: bool = True
    also: tuple[str, ...] = ()


@dataclass(frozen=True)
class Broken:
    """A row that cannot be read: entry ``variant`` of the source's ``broken``."""

    variant: int


@dataclass(frozen=True)
class Page:
    """``more``: the source says more follows; ``cursor`` False: without a way to reach it."""

    rows: tuple[Person | Broken, ...]
    more: bool = False
    cursor: bool = True


@dataclass(frozen=True)
class Failed:
    """A page that could not be read: entry ``variant`` of the source's ``failures``."""

    variant: int = 0


@dataclass(frozen=True)
class Malformed:
    """A successful answer that is not a page of users: entry ``variant`` of ``malformed``."""

    variant: int


Step = Page | Failed | Malformed


class Lookup(Enum):
    HIT = "hit"
    MISS = "miss"
    FAIL = "fail"
    MALFORMED = "malformed"


class Can(Enum):
    """A scenario not every source can express."""

    EMPTY_PAGE_WITH_CURSOR = "empty page with a cursor"
    MORE_WITHOUT_CURSOR = "more promised without a cursor"
    INACTIVE = "lists inactive users"
    SECOND_ADDRESS = "shows a user's other addresses"
    INCREMENTAL = "reads only changed users after the first sync"


def page(*rows: str | Person | Broken, more: bool = False, cursor: bool = True) -> Page:
    people = tuple(Person(r) if isinstance(r, str) else r for r in rows)
    return Page(people, more=more, cursor=cursor)


# ---------------------------------------------------------------------------
# The connectors
# ---------------------------------------------------------------------------


class Source:
    """One connector's user sync over a scripted source. ``wire`` builds the connector, ``sync`` runs
    its user sync."""

    name: str
    connector: type
    can: frozenset[Can] = frozenset()
    # Whether a listing read to its end proves whoever it left out absent.
    proves_absence = True
    # What a lookup by email that finds nobody proves; None for a source with no such lookup.
    miss: Presence | None = None
    failures: tuple[object, ...] = ("failed",)
    malformed: tuple[object, ...] = ()
    broken: tuple[object, ...] = ()

    def __init__(self, steps: list[Step], *, lookup: Lookup = Lookup.MISS, incremental: bool = False) -> None:
        self.steps = steps
        self.lookup = lookup
        self.incremental = incremental
        self.served = 0
        self.lookups = 0
        self.overrun = False
        self._last_id = 0
        self.conn = self.wire()

    def wire(self) -> object:
        raise NotImplementedError

    async def sync(self) -> None:
        raise NotImplementedError

    @property
    def listing(self) -> SourceUserListing | None:
        return self.conn._user_listing

    @property
    def read_to_the_end(self) -> bool:
        return self.served == len(self.steps)

    def next_step(self) -> Step:
        if self.read_to_the_end:
            self.overrun = True
            raise AssertionError(f"{self.name} read past the end the source gave")
        self.served += 1
        return self.steps[self.served - 1]

    def next_step_or_end(self) -> Step:
        """For a source that says "the end" with an empty page after the last one."""
        return page() if self.read_to_the_end else self.next_step()

    def rows(self, step: Page) -> list:
        rows: list = []
        for listed in step.rows:
            if isinstance(listed, Broken):
                rows.append(self.broken[listed.variant])
            else:
                self._last_id += 1
                rows.append(self.row(self._last_id, listed))
        return rows

    def row(self, uid: int, person: Person) -> object:
        raise NotImplementedError


class _GoogleDirectory(Source):
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR, Can.INACTIVE, Can.SECOND_ADDRESS})
    malformed = ({}, {"users": None}, ["not a page"])
    broken = ({"id": "b1", "primaryEmail": "bob@corp.com", "name": "not-a-mapping"},)
    # users.get of the admin the service account acts as; None is a super admin.
    admin: object = None

    def wire(self) -> MagicMock:
        conn = hooks._directory_conn(self.connector, [], admin=self.admin)
        conn._user_status = UserStatusTracker(servicenow_fakes.MemorySyncPoint())
        conn.admin_data_source.users_list = self.users_list
        return conn

    async def users_list(self, **_: object) -> object:
        step = self.next_step()
        if isinstance(step, Failed):
            raise RuntimeError("directory down")
        if isinstance(step, Malformed):
            return self.malformed[step.variant]
        return {"users": self.rows(step), **({"nextPageToken": f"t{self.served}"} if step.more else {})}

    def row(self, uid: int, person: Person) -> dict:
        row = {"id": str(uid), "aliases": list(person.also), "suspended": not person.active}
        return {**row, "primaryEmail": person.email} if person.email is not None else row

    async def sync(self) -> None:
        await self.connector._sync_users(self.conn)


class Gmail(_GoogleDirectory):
    name = "gmail"
    connector = GoogleGmailTeamConnector


class Drive(_GoogleDirectory):
    name = "drive"
    connector = GoogleDriveTeamConnector


class Dropbox(Source):
    name = "dropbox"
    connector = DropboxConnector
    can = frozenset({
        Can.EMPTY_PAGE_WITH_CURSOR, Can.MORE_WITHOUT_CURSOR, Can.INACTIVE, Can.SECOND_ADDRESS,
    })
    malformed = (SimpleNamespace(members=None, has_more=False, cursor=None), None)
    broken = (SimpleNamespace(profile=None),)

    def wire(self) -> MagicMock:
        processor = MagicMock(
            on_new_app_users=AsyncMock(), get_all_app_users=AsyncMock(return_value=[]),
            withdraw_app_user_access=AsyncMock(),
        )
        conn = hooks._connector(
            DropboxConnector, _health=MagicMock(auth_error=None), _user_listing=None, _team_emails=set(),
            data_entities_processor=processor, connector_name="DROPBOX",
            data_source=SimpleNamespace(team_members_list=self.read, team_members_list_continue=self.read),
        )
        conn.get_app_users = partial(DropboxConnector.get_app_users, conn)
        return conn

    async def read(self, _cursor: str | None = None) -> SimpleNamespace:
        step = self.next_step()
        if isinstance(step, Failed):
            return SimpleNamespace(success=False, data=None, error="internal_error")
        if isinstance(step, Malformed):
            return SimpleNamespace(success=True, data=self.malformed[step.variant], error=None)
        return SimpleNamespace(success=True, error=None, data=SimpleNamespace(
            members=self.rows(step), has_more=step.more,
            cursor=f"c{self.served}" if step.more and step.cursor else None,
        ))

    def row(self, uid: int, person: Person) -> SimpleNamespace:
        profile = SimpleNamespace(
            team_member_id=f"dbmid:{uid}", email=person.email, name=SimpleNamespace(display_name=f"m{uid}"),
            secondary_emails=[SimpleNamespace(email=e, is_verified=True) for e in person.also],
            status=SimpleNamespace(_tag="active" if person.active else "suspended", is_active=lambda: person.active),
        )
        return SimpleNamespace(profile=profile, role=SimpleNamespace(_tag="member_only"))

    async def sync(self) -> None:
        await DropboxConnector._sync_members(self.conn)


class Zoom(Source):
    name = "zoom"
    connector = ZoomConnector
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR})
    proves_absence = False  # active users only
    malformed = ({}, {"users": None}, ["not a page"])
    broken = ({"id": ["not", "an", "id"]},)

    def wire(self) -> MagicMock:
        return hooks._connector(
            ZoomConnector, _user_listing=None,
            _get_fresh_datasource=AsyncMock(return_value=SimpleNamespace(users=self.read)),
        )

    async def read(self, **_: object) -> SimpleNamespace:
        step = self.next_step()
        if isinstance(step, Failed):
            return SimpleNamespace(success=False, data=None, message="503")
        if isinstance(step, Malformed):
            return SimpleNamespace(success=True, data=self.malformed[step.variant], message=None)
        return SimpleNamespace(success=True, message=None, data={
            "users": self.rows(step), "next_page_token": f"t{self.served}" if step.more else "",
        })

    def row(self, uid: int, person: Person) -> dict:
        return {"id": f"z{uid}", "email": person.email}

    async def sync(self) -> None:
        await ZoomConnector._list_users(self.conn)


class Slack(Source):
    name = "slack"
    connector = SlackConnector
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR, Can.INACTIVE})
    malformed = ({}, {"members": None}, ["not a page"])
    broken = ({"name": "no-id", "profile": {"email": "bob@corp.com"}}, "not a member")

    def wire(self) -> MagicMock:
        conn = hooks.TestSlackUserListing()._conn([])
        conn._fresh_datasource = AsyncMock(return_value=SimpleNamespace(users_list=self.read))
        return conn

    async def read(self, **_: object) -> SimpleNamespace:
        step = self.next_step()
        if isinstance(step, Failed):
            return SimpleNamespace(success=False, error="ratelimited")
        if isinstance(step, Malformed):
            return SimpleNamespace(success=True, data=self.malformed[step.variant])
        return SimpleNamespace(success=True, data={
            "members": self.rows(step),
            "response_metadata": {"next_cursor": f"c{self.served}" if step.more else ""},
        })

    def row(self, uid: int, person: Person) -> dict:
        return hooks._slack_member(f"U{uid}", person.email, deleted=not person.active)

    async def sync(self) -> None:
        await SlackConnector._sync_users(self.conn)


class GitLab(Source):
    """The unscoped walk: each page is one group's member listing."""

    name = "gitlab"
    connector = GitLabConnector
    can = frozenset({Can.INACTIVE, Can.SECOND_ADDRESS})
    failures = (SimpleNamespace(success=False, data=None, error="403"), RuntimeError("reset"))
    # A row with no id, and one whose profile, which holds its email, cannot be read.
    broken = (SimpleNamespace(username="no-id"), SimpleNamespace(id=900, username="u900", name="U900"))

    def wire(self) -> MagicMock:
        self.profiles: dict[object, SimpleNamespace] = {}
        conn = gitlab_fakes.make_mock_connector()
        conn.runtime.ds_call = AsyncMock(side_effect=self.ds_call)
        conn.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()
        self.choose_groups(conn)
        return conn

    def choose_groups(self, conn: MagicMock) -> None:
        groups = [SimpleNamespace(id=100 + n) for n in range(len(self.steps))]
        conn.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=self.ok(groups))
        conn.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=self.ok([]))

    @staticmethod
    def ok(data: object) -> SimpleNamespace:
        return SimpleNamespace(success=True, data=data, error=None)

    async def ds_call(self, fn: object, *args: object, **_: object) -> object:
        if fn is self.conn.data_source.get_user:
            profile = self.profiles.get(args[0])
            return self.ok(profile) if profile else SimpleNamespace(success=False, data=None, error="500")
        assert fn is self.conn.data_source.list_group_members_all, f"unexpected call {fn}"
        step = self.next_step()
        if isinstance(step, Failed):
            failure = self.failures[step.variant]
            if isinstance(failure, Exception):
                raise failure
            return failure
        return self.ok(self.rows(step))

    def row(self, uid: int, person: Person) -> SimpleNamespace:
        member = {
            "id": uid, "username": f"u{uid}", "name": f"U{uid}", "state": "active" if person.active else "blocked",
        }
        # An admin's token is shown a user's own address beside the public one.
        self.profiles[uid] = SimpleNamespace(
            **member, public_email=person.email, email=person.also[0] if person.also else None,
        )
        return SimpleNamespace(**member)

    async def sync(self) -> None:
        await GitLabUsersSync(self.conn).sync_users()


class GitLabScoped(GitLab):
    """The walk over configured groups."""

    name = "gitlab-scoped"

    def choose_groups(self, conn: MagicMock) -> None:
        conn.sync_filters = {SyncFilterKey.GROUP_IDS: SimpleNamespace(
            is_empty=lambda: False, operator=FilterOperator.IN, value=[f"group-{n}" for n in range(len(self.steps))],
        )}
        conn.runtime.paged_list = AsyncMock(return_value=self.ok([]))


class JiraCloud(Source):
    name = "jira-cloud"
    connector = JiraConnector
    can = frozenset({Can.INACTIVE})
    miss = Presence.UNKNOWN  # the user search lists active users only
    failures = (403, 500)
    malformed = ({"not": "a page"}, "not a page", None)
    broken = (
        "not a row",
        {"accountId": "unknown", "accountType": "unknown"},  # what Atlassian answers in an outage
        {"accountId": "b1", "active": True, "displayName": "no account type"},
    )

    def wire(self) -> MagicMock:
        conn = jira_fakes._cloud(None, creator=CREATOR)
        conn._fetch_all_jira_users_bulk = partial(JiraConnector._fetch_all_jira_users_bulk, conn)
        conn.notify = AsyncMock()
        conn._notification_title = MagicMock(return_value="title")
        conn._call_with_retry = self.call
        return conn

    async def call(self, request: Callable[[object], Awaitable[object]], **_: object) -> object:
        return await request(SimpleNamespace(get_all_users=self.read, find_users=self.find))

    async def read(self, **_: object) -> SimpleNamespace:
        step = self.next_step_or_end()
        if isinstance(step, Failed):
            return jira_fakes._resp(self.failures[step.variant])
        if isinstance(step, Malformed):
            return jira_fakes._resp(200, self.malformed[step.variant])
        return jira_fakes._resp(200, self.rows(step))

    async def find(self, **_: object) -> SimpleNamespace:
        self.lookups += 1
        return {
            Lookup.HIT: jira_fakes._resp(200, [self.row(999, Person(LISTED_CREATOR))]),
            Lookup.MISS: jira_fakes._resp(200, []),
            Lookup.FAIL: jira_fakes._resp(500),
            Lookup.MALFORMED: jira_fakes._resp(200, {"not": "a list"}),
        }[self.lookup]

    def row(self, uid: int, person: Person) -> dict:
        row = {"accountId": f"a{uid}", "accountType": "atlassian", "active": person.active, "displayName": f"A{uid}"}
        return {**row, "emailAddress": person.email} if person.email is not None else row

    async def sync(self) -> None:
        await JiraConnector._fetch_users(self.conn, active_pipeshub_users=[])


class JiraDataCenter(Source):
    """A release with /user/list."""

    name = "jira-dc"
    connector = JiraDataCenterConnector
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR, Can.MORE_WITHOUT_CURSOR, Can.INACTIVE})
    miss = Presence.ABSENT
    failures = (403, 500)
    malformed = ({"not": "a page"}, "not a page", {"values": None}, {"values": []}, [], None)
    broken = ("not a row",)

    def wire(self) -> MagicMock:
        conn = jira_fakes._dc(self.search, user_list=True, creator=CREATOR)
        conn._fetch_users_via_list = partial(JiraDataCenterConnector._fetch_users_via_list, conn)
        conn.datasource.get_user_list_v2 = self.read
        return conn

    async def read(self, **_: object) -> SimpleNamespace:
        step = self.next_step()
        if isinstance(step, Failed):
            return jira_fakes._resp(self.failures[step.variant])
        if isinstance(step, Malformed):
            return jira_fakes._resp(200, self.malformed[step.variant])
        return jira_fakes._resp(200, {
            "values": self.rows(step), "isLast": not step.more,
            "nextCursor": f"c{self.served}" if step.more and step.cursor else None,
        })

    def search(self, **_: object) -> SimpleNamespace:
        self.lookups += 1
        return {
            Lookup.HIT: jira_fakes._resp(200, [self.row(999, Person(LISTED_CREATOR))]),
            Lookup.MISS: jira_fakes._resp(200, []),
            Lookup.FAIL: jira_fakes._resp(500),
            Lookup.MALFORMED: jira_fakes._resp(200, {"not": "a list"}),
        }[self.lookup]

    def row(self, uid: int, person: Person) -> dict:
        return jira_fakes._row(f"k{uid}", person.email, active=person.active)

    async def sync(self) -> None:
        await JiraDataCenterConnector._fetch_users(self.conn)


class ConfluenceCloud(Source):
    name = "confluence-cloud"
    connector = ConfluenceConnector
    can = frozenset({Can.INACTIVE})
    proves_absence = False  # the search pages by offset and may skip a user
    miss = Presence.UNKNOWN  # Jira's user search lists active users only
    failures = (503,)
    malformed = ({}, {"results": None}, ["not a page"])
    broken = ("not a row",)

    def wire(self) -> MagicMock:
        conn = hooks.TestConfluenceCloudUserListing()._conn(
            SimpleNamespace(search_users=self.read, search_jira_users=self.search),
        )
        conn._client_policy = None
        conn.authenticator_email = AsyncMock(return_value=CREATOR)
        conn.config_service = MagicMock(get_config=AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}}))
        for method in ("_settle_authenticator", "_jira_user_search_allowed", "_find_jira_accounts"):
            setattr(conn, method, partial(getattr(ConfluenceConnector, method), conn))
        return conn

    async def read(self, **_: object) -> SimpleNamespace:
        step = self.next_step_or_end()
        if isinstance(step, Failed):
            return SimpleNamespace(status=self.failures[step.variant], json=dict)
        body = self.malformed[step.variant] if isinstance(step, Malformed) else {"results": self.rows(step)}
        return SimpleNamespace(status=200, json=lambda: body)

    async def search(self, *_: object, **__: object) -> SimpleNamespace:
        self.lookups += 1
        return {
            Lookup.HIT: hooks._jira_rows(hooks._jira_account("a999", LISTED_CREATOR)),
            Lookup.MISS: hooks._jira_rows(),
            Lookup.FAIL: SimpleNamespace(status=500, json=list),
            Lookup.MALFORMED: SimpleNamespace(status=200, json=lambda: {"not": "a list"}),
        }[self.lookup]

    def row(self, uid: int, person: Person) -> dict:
        if not person.active:
            self.conn._inactive_account_ids.add(f"a{uid}")
        return hooks._cloud_user(f"a{uid}", person.email)

    async def sync(self) -> None:
        await ConfluenceConnector._sync_users(self.conn)


class ConfluenceDataCenter(Source):
    name = "confluence-dc"
    connector = ConfluenceDataCenterConnector
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR})
    proves_absence = False  # whether /user/list names deactivated users is not documented
    failures = (500,)
    malformed = ({}, {"results": None}, {"results": {"userKey": "u0"}}, ["not a page"])
    broken = ("not a row",)

    def wire(self) -> MagicMock:
        conn = hooks.TestConfluenceDataCenterUserListing()._conn([])
        conn._get_fresh_datasource = AsyncMock(return_value=SimpleNamespace(get_user_list_v1=self.read))
        return conn

    async def read(self, **_: object) -> SimpleNamespace:
        step = self.next_step()
        if isinstance(step, Failed):
            return SimpleNamespace(status=self.failures[step.variant], json=dict)
        if isinstance(step, Malformed):
            body = self.malformed[step.variant]
        else:
            body = {"results": self.rows(step), "_links": {"next": "/x"} if step.more else {}}
        return SimpleNamespace(status=200, json=lambda: body)

    def row(self, uid: int, person: Person) -> dict:
        return {"userKey": f"u{uid}", "email": person.email}

    async def sync(self) -> None:
        await ConfluenceDataCenterConnector._sync_users(self.conn)


class ServiceNow(Source):
    """The Table API of ``servicenow_fakes``, read one row per page so that a page can fail between rows."""

    name = "servicenow"
    connector = ServiceNowConnector
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR, Can.INACTIVE, Can.INCREMENTAL})
    miss = Presence.ABSENT
    malformed = ("a page without X-Total-Count",)
    broken = ({"sys_id": {"value": "b1"}, "email": "bob@corp.com", "active": "true"},)

    def wire(self) -> ServiceNowConnector:
        self.table = servicenow_fakes.FakeServiceNow({"sys_user": []})
        self.calls = 0
        self.stop_at: int | None = None
        for step in self.steps:
            self._store(step)
        processor = MagicMock(
            org_id="org-1", on_new_app_users=AsyncMock(), get_all_app_users=AsyncMock(return_value=[]),
            withdraw_app_user_access=AsyncMock(), full_sync_running=MagicMock(return_value=False),
        )
        with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
            conn = ServiceNowConnector(
                logger=MagicMock(), data_entities_processor=processor,
                data_store_provider=servicenow_fakes.make_data_store_provider(), config_service=AsyncMock(),
                connector_id="sn-1", scope="team", created_by="user-1",
            )
        conn.connector_name = Connectors.SERVICENOW
        stored = {"users/v2": {"done": True}, "users": {"last_sync_time": "2024-01-01 00:00:00"}}
        conn.user_sync_point = servicenow_fakes.MemorySyncPoint(stored if self.incremental else {})
        conn._users_gone = AsyncMock(return_value=set())
        conn.authenticator_email = AsyncMock(return_value=CREATOR)
        conn._get_fresh_datasource = AsyncMock(return_value=self)
        return conn

    def _store(self, step: Step) -> None:
        stored = self.table.tables["sys_user"]

        def store(row: dict) -> None:
            stored.append({**row, "sys_updated_on": f"2024-06-01 00:00:{len(stored):02d}"})

        if not isinstance(step, Page):
            # The row the failing page would have shown: the count says it is there, so the listing goes on.
            self.stop_at = len(stored)
            store({"sys_id": "unread", "email": "unread@corp.com"})
            return
        for row in self.rows(step):
            store(row)
        if not step.rows and step.more:
            # A row an ACL hides is dropped after the page is cut, so its page comes back empty.
            store({"sys_id": "hidden", "_acl_hidden": True})

    async def get_now_table_tableName(
        self, tableName: str, sysparm_query: str | None = None, **params: str | None,
    ) -> TableAPIResponse:
        if "LIKE" in (sysparm_query or ""):
            return self._answer_lookup()
        self.calls += 1
        if self.stop_at is not None and self.calls > self.stop_at:
            if any(isinstance(s, Malformed) for s in self.steps):
                return TableAPIResponse(result=[], total_count=None)
            raise servicenow_fakes.status_error(500)
        return await self.table.get_now_table_tableName(tableName, sysparm_query=sysparm_query, **params)

    def _answer_lookup(self) -> TableAPIResponse:
        self.lookups += 1
        if self.lookup is Lookup.FAIL:
            raise servicenow_fakes.status_error(500)
        rows = [TableAPIRecord(sys_id="u999", email=LISTED_CREATOR)] if self.lookup is Lookup.HIT else []
        return TableAPIResponse(result=rows, total_count=None if self.lookup is Lookup.MALFORMED else len(rows))

    @property
    def read_to_the_end(self) -> bool:
        return self.calls >= len(self.table.tables["sys_user"])

    def row(self, uid: int, person: Person) -> dict:
        return {
            "sys_id": f"u{uid}", "user_name": f"user{uid}", "email": person.email,
            "active": "true" if person.active else "false",
        }

    async def sync(self) -> None:
        with patch.object(ServiceNowDefaults, "ID_PAGE_SIZE", 1):
            await self.conn._sync_users()


class _UsersRead(Exception):
    """Stops a whole sync once its users are read."""


class Salesforce(Source):
    name = "salesforce"
    connector = SalesforceConnector
    can = frozenset({
        Can.EMPTY_PAGE_WITH_CURSOR, Can.MORE_WITHOUT_CURSOR, Can.INACTIVE, Can.INCREMENTAL,
    })
    miss = Presence.ABSENT
    malformed = ({}, {"records": None})
    broken = ("not a row",)

    def wire(self) -> SalesforceConnector:
        conn = salesforce_fakes._make_connector()
        conn.data_source = SimpleNamespace(soql_query=self.query, soql_query_next=self.read)
        conn._reinitialize_token_if_needed = AsyncMock()
        conn.drop_authenticator_link_if_listed = AsyncMock()
        conn._get_api_version = AsyncMock(return_value="59.0")
        conn._withdraw_ineligible_users = AsyncMock()
        conn._register_authenticated_identity = AsyncMock(side_effect=_UsersRead())
        conn._notify = AsyncMock()
        conn.authenticator_email = AsyncMock(return_value=CREATOR)
        checkpoint = {"lastSyncTimestamp": 1700000000000 if self.incremental else None}
        conn.user_sync_point = MagicMock(read_sync_point=AsyncMock(return_value=checkpoint))
        conn.records_sync_point = MagicMock(read_sync_point=AsyncMock(return_value={}))
        return conn

    async def query(self, *, q: str, **_: object) -> SalesforceResponse:
        return self._answer_lookup() if "WHERE Email" in q else await self.read()

    async def read(self, **_: object) -> SalesforceResponse:
        step = self.next_step()
        if isinstance(step, Failed):
            return salesforce_fakes._sf_response(False, error="down", status_code=500)
        if isinstance(step, Malformed):
            return salesforce_fakes._sf_response(True, self.malformed[step.variant])
        return salesforce_fakes._sf_response(True, {
            "records": self.rows(step), "done": not step.more,
            **({"nextRecordsUrl": f"/next/{self.served}"} if step.more and step.cursor else {}),
        })

    def _answer_lookup(self) -> SalesforceResponse:
        self.lookups += 1
        return {
            Lookup.HIT: salesforce_fakes._sf_response(True, {"records": [{"Id": "u999"}], "done": True}),
            Lookup.MISS: salesforce_fakes._sf_response(True, {"records": [], "done": True}),
            Lookup.FAIL: salesforce_fakes._sf_response(False, error="down", status_code=500),
            Lookup.MALFORMED: salesforce_fakes._sf_response(True, {"not": "a result"}),
        }[self.lookup]

    def row(self, uid: int, person: Person) -> dict:
        return {"Id": f"005{uid:012d}AAA", "Email": person.email, "IsActive": person.active}

    async def sync(self) -> None:
        loaded = AsyncMock(return_value=(FilterCollection(), FilterCollection()))
        with patch("app.connectors.sources.salesforce.connector.load_connector_filters", loaded):
            try:
                await self.conn.run_sync()
            except _UsersRead:
                return


class Notion(Source):
    name = "notion"
    connector = NotionConnector
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR, Can.MORE_WITHOUT_CURSOR})
    proves_absence = False  # guests are not listed
    malformed = ({}, {"results": None, "has_more": False}, {"results": []}, ["not a page"])
    broken = (
        {"id": "unreadable", "type": "person", "name": "its details cannot be read", "person": {}},
        {"object": "user", "id": "b1"},  # a partial user object
        {"type": "person", "name": "no id", "person": {"email": "bob@corp.com"}},
    )

    def wire(self) -> NotionConnector:
        return notion_fakes._wired(SimpleNamespace(list_users=self.read, retrieve_user=self.details))

    async def read(self, **_: object) -> MagicMock:
        step = self.next_step()
        if isinstance(step, Failed):
            return notion_fakes._make_api_response(success=False, error="503")
        if isinstance(step, Malformed):
            return notion_fakes._make_api_response(data=self.malformed[step.variant])
        return notion_fakes._make_api_response(data={
            "results": self.rows(step), "has_more": step.more,
            "next_cursor": f"c{self.served}" if step.more and step.cursor else None,
        })

    async def details(self, user_id: str) -> MagicMock:
        if user_id == "unreadable":
            return notion_fakes._make_api_response(success=False, error="403")
        return notion_fakes._make_api_response(data=notion_fakes._person(user_id))

    def row(self, uid: int, person: Person) -> dict:
        return notion_fakes._person(f"u{uid}", person.email or "")

    async def sync(self) -> None:
        await self.conn._sync_users()


class Linear(Source):
    name = "linear"
    connector = LinearConnector
    can = frozenset({Can.EMPTY_PAGE_WITH_CURSOR, Can.MORE_WITHOUT_CURSOR, Can.INACTIVE})
    malformed = ({}, {"users": {}}, {"users": {"nodes": None, "pageInfo": None}})
    broken = ("not a row",)

    def wire(self) -> LinearConnector:
        conn = linear_fakes._make_connector()
        conn.data_source = MagicMock()
        conn._get_fresh_datasource = AsyncMock(return_value=SimpleNamespace(users=self.read))
        return conn

    async def read(self, **_: object) -> GraphQLResponse:
        step = self.next_step()
        if isinstance(step, Failed):
            # A refusal Linear will not answer differently: anything transient is retried.
            return GraphQLResponse(success=False, message="bad request", status_code=400)
        if isinstance(step, Malformed):
            return GraphQLResponse(success=True, data=self.malformed[step.variant])
        return GraphQLResponse(success=True, data={"users": {
            "nodes": self.rows(step),
            "pageInfo": {
                "hasNextPage": step.more, "endCursor": f"c{self.served}" if step.more and step.cursor else None,
            },
        }})

    def row(self, uid: int, person: Person) -> dict:
        return {"id": f"u{uid}", "email": person.email, "active": person.active}

    async def sync(self) -> None:
        await self.conn._fetch_users()


class GitHubTeams(Source):
    """Each page is one organization's member listing."""

    name = "github-teams"
    connector = GitHubTeamsConnector
    can = frozenset({Can.SECOND_ADDRESS})
    failures = (
        SimpleNamespace(success=False, data=None, error="boom", status_code=500),
        SimpleNamespace(success=False, data=None, error="not found", status_code=404),
    )
    # A row with no id, and one whose profile, which holds its email, cannot be read.
    broken = (SimpleNamespace(id=None, login="noid", type="User"), SimpleNamespace(id=900, login="u900", type="User"))

    def wire(self) -> MagicMock:
        self.people: dict[object, Person] = {}
        conn = MagicMock(connector_name="GITHUB TEAMS", connector_id="connector-1", sync_filters=None)
        conn.data_entities_processor = MagicMock(
            # Fetched profiles are kept for the whole process, by org.
            org_id=f"org-{uuid4()}", get_all_app_users=AsyncMock(return_value=[]), on_new_app_users=AsyncMock(),
        )
        conn.runtime.ds_call = self.ds_call
        self.users = github_users.UsersSync(conn)
        return conn

    @staticmethod
    def ok(data: object) -> SimpleNamespace:
        return SimpleNamespace(success=True, data=data, error=None)

    async def ds_call(self, fn: object, *args: object, **_: object) -> object:
        source = self.conn.data_source
        if fn is source.list_user_orgs:
            return self.ok([SimpleNamespace(login=f"org-{n}") for n in range(len(self.steps))])
        if fn is source.list_org_outside_collaborators:
            return self.ok([])
        if fn is source.graphql_query:
            asked = re.findall(r'(u\d+): user\(login: "([^"]+)"\)', str(args[0]))
            return self.ok({alias: self._verified_emails(login) for alias, login in asked})
        if fn is source.get_user:
            person = self.people.get(args[0])
            if person is None:
                return SimpleNamespace(success=False, data=None, error="502", status_code=502)
            return self.ok(SimpleNamespace(login=args[0], email=person.email))
        assert fn is source.list_org_members, f"unexpected call {fn}"
        step = self.next_step()
        return self.failures[step.variant] if isinstance(step, Failed) else self.ok(self.rows(step))

    def _verified_emails(self, login: str) -> dict:
        person = self.people.get(login)
        emails = [person.email, *person.also] if person and person.also else []
        return {"organizationVerifiedDomainEmails": emails}

    def row(self, uid: int, person: Person) -> SimpleNamespace:
        self.people[f"u{uid}"] = person
        return SimpleNamespace(id=uid, login=f"u{uid}", type="User")

    async def sync(self) -> None:
        with patch.dict(github_users._recent_profiles, clear=True), \
                patch.dict(github_users._profile_fetch_locks, clear=True):
            await self.users.sync_users()

    @property
    def listing(self) -> SourceUserListing | None:
        return self.users.listing


SOURCES: tuple[type[Source], ...] = (
    Gmail, Drive, Dropbox, Zoom, Slack, GitLab, GitLabScoped, JiraCloud, JiraDataCenter, ConfluenceCloud,
    ConfluenceDataCenter, ServiceNow, Salesforce, Notion, Linear, GitHubTeams,
)


# ---------------------------------------------------------------------------
# Reading a source
# ---------------------------------------------------------------------------


@dataclass
class Read:
    source: Source
    error: Exception | None

    def presence(self, email: str = CREATOR) -> Presence:
        """What registration would be told. No listing proves nothing."""
        listing = self.source.listing
        return Presence.UNKNOWN if listing is None else listing.presence(email)

    def assert_seen(self, email: str) -> None:
        """``email`` was listed before anything went wrong, so the listing so far shows them. A sync
        that stopped may leave no listing at all: it then registers nobody."""
        if self.source.listing is None:
            assert self.error is not None, "a sync that went on has a listing"
        else:
            assert self.presence(email) is Presence.PRESENT


async def read(source_cls: type[Source], *steps: Step, **options: object) -> Read:
    source = source_cls(list(steps), **options)
    error: Exception | None = None
    try:
        await source.sync()
    except Exception as e:  # a sync may stop on what it read; what its listing then says is the test
        error = e
    assert not source.overrun, f"{source.name} read past the end the source gave"
    return Read(source, error)


def _each() -> pytest.MarkDecorator:
    return pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.name)


def _each_that(can: Can) -> pytest.MarkDecorator:
    return pytest.mark.parametrize("source", [s for s in SOURCES if can in s.can], ids=lambda s: s.name)


def _each_variant(attribute: str) -> pytest.MarkDecorator:
    return pytest.mark.parametrize(("source", "variant"), [
        pytest.param(source, n, id=f"{source.name}-{n}")
        for source in SOURCES for n in range(len(getattr(source, attribute)))
    ])


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


class TestACompleteListing:
    @_each()
    async def test_shows_the_creator_it_names(self, source: type[Source]) -> None:
        result = await read(source, page(OTHER, more=True), page(THIRD, LISTED_CREATOR))

        assert result.error is None
        assert result.presence() is Presence.PRESENT

    @_each()
    async def test_proves_absent_whoever_it_leaves_out_where_the_source_lists_everyone(
        self, source: type[Source],
    ) -> None:
        result = await read(source, page(OTHER, more=True), page(THIRD))

        assert result.error is None
        assert result.presence() is (Presence.ABSENT if source.proves_absence else Presence.UNKNOWN)
        assert (result.presence(OTHER), result.presence(THIRD)) == (Presence.PRESENT, Presence.PRESENT)

    @_each()
    async def test_of_nobody_proves_nothing(self, source: type[Source]) -> None:
        result = await read(source, page())

        assert result.presence() is Presence.UNKNOWN

    @_each_that(Can.INACTIVE)
    async def test_shows_a_creator_listed_as_inactive(self, source: type[Source]) -> None:
        result = await read(source, page(OTHER, Person(LISTED_CREATOR, active=False)))

        assert result.error is None
        assert result.presence() is Presence.PRESENT

    @_each_that(Can.SECOND_ADDRESS)
    async def test_shows_a_creator_listed_under_another_of_their_addresses(self, source: type[Source]) -> None:
        result = await read(source, page(OTHER, Person("public@elsewhere.com", also=(LISTED_CREATOR,))))

        assert result.error is None
        assert result.presence() is Presence.PRESENT


class TestAnIncompleteListing:
    @_each_variant("failures")
    async def test_a_page_that_fails_proves_nobody_absent(self, source: type[Source], variant: int) -> None:
        result = await read(source, page(OTHER, more=True), Failed(variant))

        assert result.source.read_to_the_end
        assert result.presence() is Presence.UNKNOWN
        result.assert_seen(OTHER)

    @_each_variant("failures")
    async def test_a_creator_named_before_a_page_failed_is_present(self, source: type[Source], variant: int) -> None:
        result = await read(source, page(LISTED_CREATOR, more=True), Failed(variant))

        result.assert_seen(CREATOR)

    @_each_variant("malformed")
    async def test_a_malformed_page_proves_nobody_absent(self, source: type[Source], variant: int) -> None:
        result = await read(source, page(OTHER, more=True), Malformed(variant))

        assert result.source.read_to_the_end
        assert result.presence() is Presence.UNKNOWN
        result.assert_seen(OTHER)

    @_each_variant("malformed")
    async def test_a_creator_named_before_a_malformed_page_is_present(
        self, source: type[Source], variant: int,
    ) -> None:
        result = await read(source, page(LISTED_CREATOR, more=True), Malformed(variant))

        result.assert_seen(CREATOR)

    @_each_that(Can.EMPTY_PAGE_WITH_CURSOR)
    async def test_an_empty_page_that_points_further_is_not_the_end(self, source: type[Source]) -> None:
        result = await read(source, page(OTHER, more=True), page(more=True), page(LISTED_CREATOR))

        assert result.presence() is not Presence.ABSENT
        result.assert_seen(OTHER)

    @_each_that(Can.EMPTY_PAGE_WITH_CURSOR)
    async def test_a_creator_named_before_an_empty_page_is_present(self, source: type[Source]) -> None:
        result = await read(source, page(LISTED_CREATOR, more=True), page(more=True), page(OTHER))

        result.assert_seen(CREATOR)

    @_each_that(Can.MORE_WITHOUT_CURSOR)
    async def test_more_with_no_way_to_reach_it_proves_nobody_absent(self, source: type[Source]) -> None:
        result = await read(source, page(OTHER, more=True), page(THIRD, more=True, cursor=False))

        assert result.source.read_to_the_end
        assert result.presence() is Presence.UNKNOWN
        result.assert_seen(OTHER)

    @_each_that(Can.MORE_WITHOUT_CURSOR)
    async def test_a_creator_named_before_more_with_no_way_to_reach_it_is_present(
        self, source: type[Source],
    ) -> None:
        result = await read(source, page(LISTED_CREATOR, more=True), page(OTHER, more=True, cursor=False))

        result.assert_seen(CREATOR)


class TestARowThatIdentifiesNobody:
    @pytest.mark.parametrize("shown", [None, "", MASKED], ids=["no-email", "empty-email", "masked-email"])
    @_each()
    async def test_a_hidden_email_proves_nobody_absent(self, source: type[Source], shown: str | None) -> None:
        result = await read(source, page(OTHER, more=True), page(Person(shown)), lookup=Lookup.FAIL)

        assert result.source.read_to_the_end
        assert result.presence() is Presence.UNKNOWN
        result.assert_seen(OTHER)

    @_each_variant("broken")
    async def test_a_row_that_cannot_be_read_proves_nobody_absent(self, source: type[Source], variant: int) -> None:
        result = await read(source, page(OTHER, more=True), page(THIRD, Broken(variant)))

        assert result.source.read_to_the_end
        assert result.presence() is Presence.UNKNOWN
        result.assert_seen(OTHER)

    @_each_variant("broken")
    async def test_a_creator_named_before_a_row_that_cannot_be_read_is_present(
        self, source: type[Source], variant: int,
    ) -> None:
        result = await read(source, page(LISTED_CREATOR, more=True), page(OTHER, Broken(variant)))

        result.assert_seen(CREATOR)


@pytest.mark.parametrize("source", [s for s in SOURCES if s.miss is not None], ids=lambda s: s.name)
class TestALookupOfTheCreator:
    """A listing that cannot tell (a hidden email, or only the users that changed) asks for them by email."""

    async def _look_up(self, source: type[Source], answer: Lookup) -> Read:
        result = await read(source, page(OTHER, Person("")), lookup=answer, incremental=True)
        assert result.source.lookups, "the creator was not looked up"
        return result

    async def test_that_finds_them_shows_them(self, source: type[Source]) -> None:
        assert (await self._look_up(source, Lookup.HIT)).presence() is Presence.PRESENT

    async def test_that_finds_nobody_proves_no_more_than_the_lookup_covers(self, source: type[Source]) -> None:
        assert (await self._look_up(source, Lookup.MISS)).presence() is source.miss

    @pytest.mark.parametrize("answer", [Lookup.FAIL, Lookup.MALFORMED], ids=lambda a: a.value)
    async def test_that_gives_no_answer_proves_nothing(self, source: type[Source], answer: Lookup) -> None:
        assert (await self._look_up(source, answer)).presence() is Presence.UNKNOWN

    async def test_that_finds_nobody_does_not_complete_a_cut_listing(self, source: type[Source]) -> None:
        result = await read(source, page(OTHER, more=True), Failed(), incremental=True)

        assert result.presence() is Presence.UNKNOWN


class TestTheServiceDeskCustomersJiraCloudLists:
    @staticmethod
    def _customer(email: str | None) -> dict:
        row = {"accountId": "customer-1", "accountType": "customer", "active": True, "displayName": "C"}
        return {**row, "emailAddress": email} if email else row

    async def _read(self, monkeypatch: pytest.MonkeyPatch, email: str | None) -> Read:
        monkeypatch.setattr(JiraCloud, "broken", (self._customer(email),))
        return await read(JiraCloud, page(OTHER, Broken(0)), lookup=Lookup.FAIL)

    async def test_are_people_the_listing_shows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert (await self._read(monkeypatch, LISTED_CREATOR)).presence() is Presence.PRESENT

    async def test_may_be_the_creator_when_their_email_is_hidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert (await self._read(monkeypatch, None)).presence() is Presence.UNKNOWN

    async def test_with_another_email_leave_the_creator_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert (await self._read(monkeypatch, THIRD)).presence() is Presence.ABSENT

    async def test_get_no_app_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(JiraCloud, "broken", (self._customer(THIRD),))
        jira = JiraCloud([page(OTHER, Broken(0))])

        users = await JiraConnector._fetch_users(jira.conn, active_pipeshub_users=[])

        assert [u.email for u in users] == [OTHER]


@_each_that(Can.INCREMENTAL)
async def test_a_read_of_only_the_changed_users_proves_nobody_absent(source: type[Source]) -> None:
    result = await read(source, page(OTHER, more=True), page(THIRD), lookup=Lookup.FAIL, incremental=True)

    assert result.error is None
    assert result.presence() is Presence.UNKNOWN


# ---------------------------------------------------------------------------
# What only one source can do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("admin", [
    {"id": "0", "primaryEmail": "admin@corp.com", "isAdmin": False},
    {"id": "0", "primaryEmail": "admin@corp.com"},
    RuntimeError("not authorized"),
], ids=["delegated-admin", "admin-status-not-shown", "admin-unreadable"])
@pytest.mark.parametrize("source", [Gmail, Drive], ids=lambda s: s.name)
async def test_a_google_listing_that_may_be_a_delegated_admins_never_proves_absence(
    source: type[_GoogleDirectory], admin: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source, "admin", admin)

    result = await read(source, page(OTHER, more=True), page(THIRD))

    assert result.error is None
    assert result.presence() is Presence.UNKNOWN
    assert result.presence(OTHER) is Presence.PRESENT


def _injecting_the_creator(source: type[GitLab], *steps: Step) -> GitLab:
    gitlab = source(list(steps))
    gitlab.conn._gitlab_user_id, gitlab.conn.creator_email = 9, CREATOR
    gitlab.conn._creator_token_view = True
    return gitlab


@pytest.mark.parametrize("source", [GitLab, GitLabScoped], ids=lambda s: s.name)
class TestTheCreatorGitLabInjects:
    async def test_is_never_a_listed_row(self, source: type[GitLab]) -> None:
        gitlab = _injecting_the_creator(source, page(OTHER))

        await gitlab.sync()

        written = [
            user.source_user_id
            for call in gitlab.conn.data_entities_processor.on_new_app_users.await_args_list for user in call.args[0]
        ]
        assert "9" in written, "the creator was not injected"
        assert gitlab.listing.presence(CREATOR) is Presence.ABSENT

    async def test_does_not_make_a_failed_walk_a_listing(self, source: type[GitLab]) -> None:
        gitlab = _injecting_the_creator(source, Failed())

        await gitlab.sync()

        assert gitlab.listing.presence(CREATOR) is Presence.UNKNOWN


# ---------------------------------------------------------------------------
# Every connector is here
# ---------------------------------------------------------------------------


REGISTER = "register_authenticated_source_user"
CONNECTOR_SOURCES = Path(__file__).resolve().parents[4] / "app" / "connectors" / "sources"
# A personal connector's account is its user's own: there is no listing to prove anything with.
PERSONAL = frozenset({
    "DropboxIndividualConnector", "GoogleDriveIndividualConnector", "GoogleGmailIndividualConnector",
})
# Connector class -> the test file that covers it, from tests/unit/connectors. Both go in the docstring too.
NOT_COVERED: dict[str, str] = {}


@dataclass(frozen=True)
class Registration:
    where: str
    owner: str
    passes_listing: bool


def _registrations() -> Iterator[Registration]:
    """Every mention of the register method under the connector sources, by the class it is in."""
    for path in sorted(CONNECTOR_SOURCES.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if REGISTER not in text:
            continue
        tree = ast.parse(text)
        calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
        owners = {
            id(inner): cls.name for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef) for inner in ast.walk(cls)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr == REGISTER):
                continue
            call = calls.get(id(node))
            listing = next((k.value for k in call.keywords if k.arg == "listing"), None) if call else None
            yield Registration(
                where=f"{path.relative_to(CONNECTOR_SOURCES)}:{node.lineno}",
                owner=owners.get(id(node), "<module>"),
                passes_listing=listing is not None and not isinstance(listing, ast.Constant),
            )


class TestNoConnectorSkipsTheContract:
    def test_the_connectors_are_found(self) -> None:
        assert {r.owner for r in _registrations()} >= PERSONAL | {s.connector.__name__ for s in SOURCES}

    def test_every_connector_passes_its_listing_unless_it_is_personal(self) -> None:
        without = [r for r in _registrations() if not r.passes_listing and r.owner not in PERSONAL]

        assert without == [], "a connector that registers with no listing can never prove its authenticator absent"

    def test_every_connector_that_passes_a_listing_has_a_source_here(self) -> None:
        covered = {s.connector.__name__ for s in SOURCES}
        missing = [
            r for r in _registrations()
            if r.passes_listing and r.owner not in covered and r.owner not in NOT_COVERED
        ]

        assert missing == [], "add a Source for this connector, or name it in NOT_COVERED and the module docstring"

    def test_what_is_not_covered_is_named_in_the_docstring_with_a_test_file_that_exists(self) -> None:
        covered = {s.connector.__name__ for s in SOURCES}
        for connector, test_file in NOT_COVERED.items():
            assert connector in __doc__ and test_file in __doc__
            assert (Path(__file__).resolve().parents[1] / test_file).is_file()
            assert connector not in covered, f"{connector} has a Source: take it off NOT_COVERED"

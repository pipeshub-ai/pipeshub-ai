"""Jira Data Center issue security levels, driven end to end over a fake Jira REST API.

An issue that has a security level is shown by Jira only to the holders of that level who
can also browse the project. The connector files such an issue as RESTRICTED and grants
it to the level's holders; an issue with no level is left to its project. Nothing about a
level moves an issue's ``updated``, so a level that gains a holder, a scheme that changes,
and every issue stored before levels were applied are read again from the checkpoint.
"""

import json
from typing import Any, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    json_response,
)
from test_jira_dc_sync_behaviour import (
    API,
    EPIC_FIELD,
    IssueSearch,
    JiraRecordsDb,
    attachment,
    make_connector,
    stub_site,
    ts,
)

from app.config.constants.arangodb import AccessRule
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.models.entities import AppUser, RecordType, TicketRecord
from app.sources.client.http.http_client import HTTPClient

SCHEME = "10050"
SECRET, TEAM, NOBODY = "10101", "10102", "10103"


@pytest.fixture
def db() -> JiraRecordsDb:
    return JiraRecordsDb()


@pytest.fixture
def store() -> FakeCheckpointStore:
    return FakeCheckpointStore()


@pytest.fixture
def jira(atlassian_api: AtlassianApiStub, monkeypatch: pytest.MonkeyPatch) -> AtlassianApiStub:
    """Every real ``HTTPClient`` talks to the stub (init already makes a request)."""

    async def _ensure_client(self: HTTPClient) -> httpx.AsyncClient:
        if self.client is None:
            atlassian_api.install(self)
        return self.client

    monkeypatch.setattr(HTTPClient, "_ensure_client", _ensure_client)
    return atlassian_api


def holder(kind: str, **parts: object) -> dict[str, Any]:
    return {"type": kind, **parts}


ALICE = holder("user", parameter="alice-key", user={"key": "alice-key", "name": "alice"})
CAROL_BY_NAME = holder("user", parameter="carol", user={"name": "carol"})
DEVS = holder("group", parameter="devs", value="gid-9")  # Data Center names a group by its name


class SecuredSearch(IssueSearch):
    """A Jira that answers by JQL: ``source`` is everything in it, ``recent`` what an incremental
    read (``updated >= -Nm``) returns, and a clause naming children returns the matching ones."""

    def __init__(self) -> None:
        super().__init__()
        self.source: dict[str, dict[str, Any]] = {}
        self.recent: list[dict[str, Any]] = []
        self.refuse_children = False
        self.plain_calls = 0  # reads of every stored issue, which a test can make fail from the Nth on
        self.fail_plain_from: int | None = None

    def put(self, *issues: dict[str, Any]) -> None:
        for one in issues:
            self.source[one["id"]] = one

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        jql = body["jql"]
        self.jql.append(jql)
        if "parent in (" in jql or "cf[" in jql:
            if self.refuse_children:
                return json_response({"errorMessages": ["JQL refused"]}, status=400)
            if "parent in (" in jql:
                wanted = {w.strip() for w in jql.split("parent in (")[1].split(")")[0].split(",")}
                rows = [i for i in self.source.values() if (i["fields"].get("parent") or {}).get("id") in wanted]
            else:
                wanted = {w.strip() for w in jql.split("] in (")[1].split(")")[0].split(",")}
                rows = [i for i in self.source.values() if i["fields"].get(EPIC_FIELD) in wanted]
        elif "updated >=" in jql:
            rows = list(self.recent)
        else:
            self.plain_calls += 1
            if self.fail_plain_from is not None and self.plain_calls >= self.fail_plain_from:
                return json_response({"errorMessages": ["boom"]}, status=500)
            rows = list(self.source.values())
        return json_response({"issues": rows, "total": len(rows)})


@pytest.fixture
def jql_search() -> SecuredSearch:
    return SecuredSearch()


def sec_issue(
    iid: str, key: str, *, level: Optional[str] = None, updated: str = ts(1), kind: str = "Task",
    parent: Optional[str] = None, epic: Optional[str] = None, reporter: str = "carol", assignee: Optional[str] = None,
    attachments: Optional[list[dict[str, Any]]] = None, **extra: object,
) -> dict[str, Any]:
    """A DC issue in a project with a scheme: ``security`` is present, null when it has no level."""
    people = {"alice": "alice-key", "bob": "bob-key", "carol": "carol-key"}
    fields: dict[str, Any] = {
        "summary": f"Summary of {key}", "status": {"name": "Open"}, "priority": {"name": "High"},
        "issuetype": {"name": kind, "subtask": kind == "Sub-task"},
        "project": {"id": "10000", "key": "ENG"}, "created": ts(1, 8), "updated": updated,
        "creator": {"key": "alice-key", "name": "alice"},
        "reporter": {"key": people[reporter], "name": reporter},
        "assignee": {"key": people[assignee], "name": assignee} if assignee else None,
        "attachment": attachments or [], "security": {"id": level, "name": f"level {level}"} if level else None,
        **extra,
    }
    if parent:
        fields["parent"] = {"id": parent, "key": "ENG-1"}
    if epic:
        fields[EPIC_FIELD] = epic
    return {"id": iid, "key": key, "fields": fields}


def serve_scheme(
    jira: AtlassianApiStub, levels: dict[str, list[dict[str, Any]]], scheme: str = SCHEME, *, members: object = None,
) -> None:
    """The project's scheme and, unless ``members`` replaces it, the holders of each level."""
    jira.on("GET", f"{API}/project/ENG/issuesecuritylevelscheme", {
        "id": scheme, "name": "Scheme", "levels": [{"id": lid, "name": f"level {lid}"} for lid in levels],
    })
    rows = [{"issueSecurityLevelId": lid, "holder": h} for lid, hs in levels.items() for h in hs]
    jira.on("GET", f"{API}/issuesecurityschemes/{scheme}/members",
            members if members is not None else {"values": rows, "isLast": True})


def no_scheme(jira: AtlassianApiStub) -> None:
    jira.on("GET", f"{API}/project/ENG/issuesecuritylevelscheme",
            json_response({"errorMessages": ["The project has no issue security scheme."], "errors": {}}, status=404))


def readers(db: JiraRecordsDb, external_id: str) -> set[tuple[str, str]]:
    """Who the connector granted the issue to: (kind, email or id)."""
    return {
        (str(p.entity_type.value if hasattr(p.entity_type, "value") else p.entity_type), p.email or p.external_id)
        for p in db.record_permissions.get(external_id, [])
    }


def hidden_from_project(record: TicketRecord) -> bool:
    return record.access_rule == AccessRule.RESTRICTED


def store_open(connector: JiraDataCenterConnector, db: JiraRecordsDb, store: FakeCheckpointStore, *issues: dict) -> None:
    """What the previous connector left behind: the issues stored open and a project checkpoint with no level digest."""
    for one in issues:
        db.records[one["id"]] = TicketRecord(
            id=f"t-{one['id']}", org_id="org-1", external_record_id=one["id"], record_name=one["key"],
            record_type=RecordType.TICKET, origin="CONNECTOR", connector_name="JIRA DATA CENTER",
            connector_id="jira-dc-1", external_record_group_id="10000", version=3, mime_type="text/plain",
            source_updated_at=connector._parse_jira_timestamp(one["fields"]["updated"]),
        )
        db.record_permissions[one["id"]] = []
    store.sync_points["org-1/jira-dc-1/records/project_ENG"] = {
        "last_sync_time": connector._parse_jira_timestamp(ts(3)),
        "last_issue_updated": connector._parse_jira_timestamp(ts(2)),
    }


async def sync(connector: JiraDataCenterConnector, jql_search: SecuredSearch, recent: Optional[list[dict[str, Any]]] = None) -> None:
    jql_search.recent = list(recent or [])
    jql_search.jql.clear()
    jql_search.plain_calls = 0
    await connector.run_sync()


class TestLevelHolders:
    async def test_a_secured_issue_is_granted_to_each_kind_of_holder_and_a_plain_one_stays_open(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [
            ALICE, CAROL_BY_NAME, DEVS,
            holder("projectRole", parameter="10002", projectRole={"id": "10002", "name": "Developers"}),
            holder("projectLead"),
            holder("reporter"), holder("assignee"),
            holder("applicationRole", parameter="jira-software"),
            holder("userCustomField", parameter=EPIC_FIELD),
            holder("groupCustomField", parameter="customfield_10200"),
        ]})
        jql_search.put(
            sec_issue("1", "ENG-1"),
            sec_issue("2", "ENG-2", level=SECRET, reporter="bob", assignee="alice",
                      **{EPIC_FIELD: {"key": "bob-key", "name": "bob"}, "customfield_10200": {"name": "ops"}}),
        )
        jql_search.recent = list(jql_search.source.values())
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert not hidden_from_project(db.records["1"]) and readers(db, "1") == set()
        assert hidden_from_project(db.records["2"])
        assert readers(db, "2") == {
            ("USER", "alice@example.com"),            # a user holder by key, and the assignee
            ("USER", "carol@example.com"),            # a user holder named by username only
            ("GROUP", "devs"),
            ("ROLE", "ENG_10002"),
            ("ROLE", "ENG_projectLead"),
            ("USER", "bob@example.com"),              # the reporter, and the user field
            ("GROUP", "jira-software-users"),         # the application role, as its groups
            ("GROUP", "ops"),                         # the group field
        }

    async def test_an_attachment_and_a_sub_task_of_a_secured_issue_follow_it(self, jira, db, store, jql_search) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        jql_search.put(
            sec_issue("2", "ENG-2", level=SECRET, attachments=[attachment("200")]),
            sec_issue("3", "ENG-3", level=SECRET, kind="Sub-task", parent="2"),
        )
        jql_search.recent = list(jql_search.source.values())
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        file = db.records["attachment_200"]
        assert file.parent_external_record_id == "2" and file.inherit_permissions
        assert db.record_permissions["attachment_200"] == []  # it takes its issue's readers, none of its own
        sub = db.records["3"]
        assert sub.parent_external_record_id == "2" and hidden_from_project(sub)
        assert readers(db, "3") == {("USER", "alice@example.com")}

    async def test_a_holder_who_cannot_browse_the_project_is_granted_but_the_project_still_gates_the_issue(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {NOBODY: [holder("user", parameter="ghost", user={"name": "ghost", "emailAddress": "ghost@example.com"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=NOBODY))
        jql_search.recent = list(jql_search.source.values())
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        # The issue inherits the project, so a holder outside it reads nothing; the grant is still the holder's.
        assert db.records["2"].inherit_permissions is True
        assert hidden_from_project(db.records["2"])
        assert readers(db, "2") == {("USER", "ghost@example.com")}


class TestUpgrade:
    async def test_an_issue_stored_open_is_closed_by_the_first_sync_after_upgrade_with_no_change_at_source(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE], TEAM: [DEVS]})
        secured = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        plain = sec_issue("1", "ENG-1", updated=ts(1))
        jql_search.put(plain, secured)
        connector, _ = await make_connector(db, store)
        # What the previous connector left behind: both issues stored open, a project checkpoint with no
        # level digest, and nothing changed at the source since.
        for one in (plain, secured):
            db.records[one["id"]] = TicketRecord(
                id=f"t-{one['id']}", org_id="org-1", external_record_id=one["id"], record_name=one["key"],
                record_type=RecordType.TICKET, origin="CONNECTOR", connector_name="JIRA DATA CENTER",
                connector_id="jira-dc-1", external_record_group_id="10000", version=3, mime_type="text/plain",
                source_updated_at=connector._parse_jira_timestamp(one["fields"]["updated"]),
            )
            db.record_permissions[one["id"]] = []
        store.sync_points["org-1/jira-dc-1/records/project_ENG"] = {
            "last_sync_time": connector._parse_jira_timestamp(ts(3)),
            "last_issue_updated": connector._parse_jira_timestamp(ts(2)),
        }
        batches_before = len(db.record_batches)

        await sync(connector, jql_search, recent=[])

        assert hidden_from_project(db.records["2"]) and db.records["2"].id == "t-2" and db.records["2"].version == 3
        assert readers(db, "2") == {("USER", "alice@example.com")}
        written = {r.external_record_id for batch in db.record_batches[batches_before:] for r in batch}
        assert written == {"2"}, "only the secured issue is written; the plain one is not touched"
        assert set(json.loads(store.values_for("project_ENG")["security_levels"])) == {SECRET, TEAM}

        # It is a one-time read: the next sync with nothing changed writes nothing.
        batches_after = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert len(db.record_batches) == batches_after
        assert not any("level" in q.lower() for q in jql_search.jql)

    async def test_a_project_with_no_scheme_is_not_read_again_on_upgrade(self, jira, db, store, jql_search) -> None:
        stub_site(jira, jql_search)  # the project has no scheme
        plain = sec_issue("1", "ENG-1", updated=ts(1))
        jql_search.put(plain)
        connector, _ = await make_connector(db, store)
        store.sync_points["org-1/jira-dc-1/records/project_ENG"] = {
            "last_sync_time": connector._parse_jira_timestamp(ts(3)),
            "last_issue_updated": connector._parse_jira_timestamp(ts(2)),
        }

        await sync(connector, jql_search, recent=[])

        assert db.record_batches == [] and len(jql_search.jql) == 1  # the incremental read, and nothing else


class TestChangesAtSource:
    async def test_a_level_added_changed_and_removed_on_an_issue_takes_effect(self, jira, db, store, jql_search) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE], TEAM: [DEVS]})
        connector, _ = await make_connector(db, store)

        plain = sec_issue("2", "ENG-2", updated=ts(1))
        jql_search.put(plain)
        await sync(connector, jql_search, recent=[plain])
        assert not hidden_from_project(db.records["2"])

        added = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        jql_search.put(added)
        await sync(connector, jql_search, recent=[added])
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == {("USER", "alice@example.com")}

        moved = sec_issue("2", "ENG-2", level=TEAM, updated=ts(3))
        jql_search.put(moved)
        await sync(connector, jql_search, recent=[moved])
        assert readers(db, "2") == {("GROUP", "devs")}

        cleared = sec_issue("2", "ENG-2", updated=ts(4))
        jql_search.put(cleared)
        await sync(connector, jql_search, recent=[cleared])
        assert not hidden_from_project(db.records["2"]) and readers(db, "2") == set()

    async def test_a_level_that_gains_a_holder_rereads_only_its_own_issues(self, jira, db, store, jql_search) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE], TEAM: [DEVS]})
        jql_search.put(
            sec_issue("2", "ENG-2", level=SECRET), sec_issue("3", "ENG-3", level=TEAM), sec_issue("1", "ENG-1"),
        )
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert readers(db, "2") == {("USER", "alice@example.com")}
        batches_before = len(db.record_batches)

        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME], TEAM: [DEVS]})  # no issue's `updated` moves
        await sync(connector, jql_search, recent=[])

        assert readers(db, "2") == {("USER", "alice@example.com"), ("USER", "carol@example.com")}
        written = {r.external_record_id for batch in db.record_batches[batches_before:] for r in batch}
        assert written == {"2"}

    async def test_a_level_that_loses_its_last_holder_or_a_scheme_swap_reads_the_whole_project(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        secured = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(secured)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[secured])
        assert hidden_from_project(db.records["2"])

        # The issue is moved to another scheme's level without a new `updated`, and the old level is gone.
        swapped = sec_issue("2", "ENG-2", level="20001")
        jql_search.put(swapped)
        serve_scheme(jira, {"20001": [DEVS]}, scheme="20050")
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("GROUP", "devs")}

        # The scheme is taken off the project and the issue loses its level without a new `updated`.
        jql_search.put(sec_issue("2", "ENG-2"))
        no_scheme(jira)
        await sync(connector, jql_search, recent=[])
        assert not hidden_from_project(db.records["2"]) and readers(db, "2") == set()


class TestFailClosed:
    async def test_a_server_that_does_not_list_level_holders_hides_secured_issues_and_says_so(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, members=json_response({"message": "Not Found"}, status=404))
        jql_search.put(sec_issue("1", "ENG-1"), sec_issue("2", "ENG-2", level=SECRET))
        jql_search.recent = list(jql_search.source.values())
        connector, notes = await make_connector(db, store)

        await connector.run_sync()

        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert not hidden_from_project(db.records["1"])
        warning = next(n for n in notes.sent if "issue security levels" in n["title"])
        assert "does not list who holds" in warning["message"] and "ENG" in warning["message"]

    async def test_an_account_refused_the_scheme_hides_secured_issues_and_names_the_permission(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        jira.on("GET", f"{API}/project/ENG/issuesecuritylevelscheme",
                json_response({"errorMessages": ["You do not have permission"]}, status=403))
        jql_search.put(sec_issue("1", "ENG-1"), sec_issue("2", "ENG-2", level=SECRET))
        jql_search.recent = list(jql_search.source.values())
        connector, notes = await make_connector(db, store)

        await connector.run_sync()

        assert hidden_from_project(db.records["2"]) and not hidden_from_project(db.records["1"])
        warning = next(n for n in notes.sent if "issue security levels" in n["title"])
        assert "Administer Jira" in warning["message"] and "hidden from everyone" in warning["message"]

    async def test_an_issue_changed_while_its_level_cannot_be_read_is_closed_and_retried_from_a_marker_saved_first(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        first = sec_issue("2", "ENG-2", level=SECRET, updated=ts(1))
        jql_search.put(first)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[first])
        assert readers(db, "2") == {("USER", "alice@example.com")}

        seen_by_search: list[str] = []
        jira.on("GET", f"{API}/issuesecurityschemes/{SCHEME}/members", json_response({"errorMessages": ["busy"]}, status=503))
        changed = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        jql_search.put(changed)
        original = jql_search.__call__

        def spy(request: httpx.Request) -> httpx.Response:
            seen_by_search.append(store.values_for("project_ENG").get("security_levels", ""))
            return original(request)

        jira.on("POST", f"{API}/search", spy)
        await sync(connector, jql_search, recent=[changed])

        assert "retry" in seen_by_search[0], "the marker is saved before the first page is read"
        # Jira changed the issue, so it may have another level now: the old level's holders must not keep it.
        assert hidden_from_project(db.records["2"])
        assert readers(db, "2") == set()

        jira.on("POST", f"{API}/search", jql_search)
        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com"), ("USER", "carol@example.com")}
        assert "*" not in json.loads(store.values_for("project_ENG")["security_levels"])


    async def test_issues_stored_open_are_closed_once_when_the_scheme_cannot_be_read(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        jira.on("GET", f"{API}/project/ENG/issuesecuritylevelscheme",
                json_response({"errorMessages": ["no permission"]}, status=403))
        secured, plain = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)), sec_issue("1", "ENG-1", updated=ts(1))
        jql_search.put(plain, secured)
        connector, _ = await make_connector(db, store)
        for one in (plain, secured):
            db.records[one["id"]] = TicketRecord(
                id=f"t-{one['id']}", org_id="org-1", external_record_id=one["id"], record_name=one["key"],
                record_type=RecordType.TICKET, origin="CONNECTOR", connector_name="JIRA DATA CENTER",
                connector_id="jira-dc-1", external_record_group_id="10000", version=3, mime_type="text/plain",
                source_updated_at=connector._parse_jira_timestamp(one["fields"]["updated"]),
            )
        store.sync_points["org-1/jira-dc-1/records/project_ENG"] = {
            "last_sync_time": connector._parse_jira_timestamp(ts(3)),
            "last_issue_updated": connector._parse_jira_timestamp(ts(2)),
        }

        await sync(connector, jql_search, recent=[])

        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert not hidden_from_project(db.records["1"])
        batches = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert len(db.record_batches) == batches, "a lasting refusal does not repeat the walk"


class TestUpgradeWhileTheLevelsCannotBeRead:
    """Issues stored open have no grants of their own to keep, so they are hidden until the levels can be read."""

    async def _stored(self, jira, db, store, jql_search) -> tuple[JiraDataCenterConnector, Any]:
        stub_site(jira, jql_search)
        secured, plain = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)), sec_issue("1", "ENG-1", updated=ts(1))
        jql_search.put(plain, secured)
        connector, notes = await make_connector(db, store)
        store_open(connector, db, store, plain, secured)
        return connector, notes

    async def test_a_transient_failure_reading_the_holders_hides_the_stored_leveled_issues_until_it_passes(
        self, jira, db, store, jql_search,
    ) -> None:
        connector, notes = await self._stored(jira, db, store, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, members=json_response({"errorMessages": ["busy"]}, status=503))

        await sync(connector, jql_search, recent=[])

        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert not hidden_from_project(db.records["1"])
        assert "hidden from everyone" in next(n for n in notes.sent if "issue security levels" in n["title"])["message"]
        assert json.loads(store.values_for("project_ENG")["security_levels"]) == {"*": "held"}
        batches = len(db.record_batches)
        searches = len(jql_search.jql)
        notices = len(notes.sent)
        await sync(connector, jql_search, recent=[])
        assert len(db.record_batches) == batches, "a read that keeps failing does not repeat the pass"
        assert len(jql_search.jql) == 1 and searches > 1, "and does not walk the project again"
        assert json.loads(store.values_for("project_ENG")["security_levels"]) == {"*": "held"}
        again = [n for n in notes.sent[notices:] if "issue security levels" in n["title"]]
        assert "hidden from everyone" in again[0]["message"] and "keep the access" not in again[0]["message"]

        serve_scheme(jira, {SECRET: [ALICE]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com")}
        assert SECRET in json.loads(store.values_for("project_ENG")["security_levels"])

    async def test_an_issue_that_fails_to_build_in_the_closing_pass_is_closed_by_the_next_sync(
        self, jira, db, store, jql_search,
    ) -> None:
        connector, _ = await self._stored(jira, db, store, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, members=json_response({"errorMessages": ["busy"]}, status=503))
        real = connector._extract_issue_data_with_parent
        failing = {"on": True}

        async def extract(issue: dict, users: dict) -> dict:
            if failing["on"] and issue["id"] == "2":
                raise RuntimeError("cannot build")
            return await real(issue, users)

        connector._extract_issue_data_with_parent = extract

        await sync(connector, jql_search, recent=[])

        assert not hidden_from_project(db.records["2"]), "it stayed open"
        assert json.loads(store.values_for("project_ENG")["security_levels"]) == {"*": "retry"}, "not marked done"

        failing["on"] = False
        await sync(connector, jql_search, recent=[])
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert json.loads(store.values_for("project_ENG")["security_levels"]) == {"*": "held"}

    async def test_a_level_naming_application_roles_that_cannot_be_read_is_hidden_until_they_can(
        self, jira, db, store, jql_search,
    ) -> None:
        connector, _ = await self._stored(jira, db, store, jql_search)
        serve_scheme(jira, {SECRET: [holder("applicationRole", parameter="jira-software")]})
        jira.on("GET", f"{API}/applicationrole", json_response({"errorMessages": ["unauthorized"]}, status=401))

        await sync(connector, jql_search, recent=[])

        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert not hidden_from_project(db.records["1"])

        jira.on("GET", f"{API}/applicationrole", json_response([{"key": "jira-software", "groups": ["jira-software-users"]}]))
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("GROUP", "jira-software-users")}


class TestAnUnclearNotFoundOnTheSchemeRoute:
    """A 404 that does not say the project has no scheme (a proxy, a missing route, an HTML page) is unknown:
    the stored state is kept, nothing is cleared, and a project never tightened hides its leveled issues."""

    GENERIC = [
        json_response({"message": "Not Found"}, status=404),
        json_response({"errorMessages": ["Not Found"]}, status=404),
        httpx.Response(404, content=b"<html>Not Found</html>", headers={"content-type": "text/html"}),
    ]

    @pytest.mark.parametrize("answer", GENERIC, ids=["json", "messages", "html"])
    async def test_a_tightened_project_keeps_its_grants_and_digests(self, jira, db, store, jql_search, answer) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        secured = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(secured)
        connector, notes = await make_connector(db, store)
        await sync(connector, jql_search, recent=[secured])
        digests = store.values_for("project_ENG")["security_levels"]
        assert readers(db, "2") == {("USER", "alice@example.com")}

        jira.on("GET", f"{API}/project/ENG/issuesecuritylevelscheme", answer)
        await sync(connector, jql_search, recent=[])

        assert readers(db, "2") == {("USER", "alice@example.com")} and hidden_from_project(db.records["2"])
        assert json.loads(store.values_for("project_ENG")["security_levels"]) == {**json.loads(digests), "*": "retry"}
        message = next(n for n in notes.sent if "issue security levels" in n["title"])["message"]
        assert "keep the access they had" in message and "not-found" in message

    @pytest.mark.parametrize("answer", GENERIC, ids=["json", "messages", "html"])
    async def test_a_project_never_tightened_hides_its_leveled_issues(self, jira, db, store, jql_search, answer) -> None:
        stub_site(jira, jql_search)
        secured, plain = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)), sec_issue("1", "ENG-1", updated=ts(1))
        jql_search.put(plain, secured)
        connector, notes = await make_connector(db, store)
        store_open(connector, db, store, plain, secured)
        jira.on("GET", f"{API}/project/ENG/issuesecuritylevelscheme", answer)

        await sync(connector, jql_search, recent=[])

        assert hidden_from_project(db.records["2"]) and not hidden_from_project(db.records["1"])
        assert "hidden from everyone" in next(n for n in notes.sent if "issue security levels" in n["title"])["message"]


class TestHierarchy:
    async def test_a_sub_task_follows_its_parent_when_the_parent_gains_or_loses_a_level(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        parent, child = sec_issue("2", "ENG-2", updated=ts(1)), sec_issue("3", "ENG-3", kind="Sub-task", parent="2", updated=ts(1))
        jql_search.put(parent, child)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[parent, child])
        assert not hidden_from_project(db.records["3"])

        # Jira copies the parent's level onto the sub-task without moving the sub-task's `updated`.
        parent_secured = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        child_secured = sec_issue("3", "ENG-3", level=SECRET, kind="Sub-task", parent="2", updated=ts(1))
        jql_search.put(parent_secured, child_secured)
        await sync(connector, jql_search, recent=[parent_secured])
        assert hidden_from_project(db.records["3"]) and readers(db, "3") == {("USER", "alice@example.com")}

        parent_open = sec_issue("2", "ENG-2", updated=ts(3))
        child_open = sec_issue("3", "ENG-3", kind="Sub-task", parent="2", updated=ts(1))
        jql_search.put(parent_open, child_open)
        await sync(connector, jql_search, recent=[parent_open])
        assert not hidden_from_project(db.records["3"]) and readers(db, "3") == set()

    async def test_a_story_is_filed_under_a_leveled_epic_only_when_its_readers_can_read_the_epic(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        epic = sec_issue("5", "ENG-5", kind="Epic", level=SECRET)
        open_story = sec_issue("6", "ENG-6", kind="Story", epic="ENG-5")
        same_level_story = sec_issue("7", "ENG-7", kind="Story", epic="ENG-5", level=SECRET)
        jql_search.put(epic, open_story, same_level_story)
        jira.on("GET", f"{API}/issue/ENG-5", {"id": "5", "key": "ENG-5"})
        jira.on("GET", f"{API}/issue/5", epic)
        connector, _ = await make_connector(db, store)

        await sync(connector, jql_search, recent=list(jql_search.source.values()))

        # Everyone who reads the open story may not read the epic, so it hangs off the project; the
        # story in the epic's level (one holder on every issue) stays under the epic.
        assert db.records["6"].parent_external_record_id is None and not hidden_from_project(db.records["6"])
        assert db.records["7"].parent_external_record_id == "5"


    async def test_stories_leave_an_epic_that_gains_a_level_and_return_when_it_loses_it(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        jira.on("GET", f"{API}/issue/ENG-5", {"id": "5", "key": "ENG-5"})
        epic, story = sec_issue("5", "ENG-5", kind="Epic"), sec_issue("6", "ENG-6", kind="Story", epic="ENG-5")
        jql_search.put(epic, story)
        jira.on("GET", f"{API}/issue/5", epic)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[epic, story])
        assert db.records["6"].parent_external_record_id == "5"

        # The epic's level moves no story's `updated`.
        leveled = sec_issue("5", "ENG-5", kind="Epic", level=SECRET, updated=ts(2))
        jql_search.put(leveled)
        await sync(connector, jql_search, recent=[leveled])
        assert db.records["6"].parent_external_record_id is None and not hidden_from_project(db.records["6"])

        open_again = sec_issue("5", "ENG-5", kind="Epic", updated=ts(3))
        jql_search.put(open_again)
        await sync(connector, jql_search, recent=[open_again])
        assert db.records["6"].parent_external_record_id == "5"

    async def test_an_epic_changed_with_no_level_before_or_after_does_not_read_its_stories_again(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        jira.on("GET", f"{API}/issue/ENG-5", {"id": "5", "key": "ENG-5"})
        epic, story = sec_issue("5", "ENG-5", kind="Epic"), sec_issue("6", "ENG-6", kind="Story", epic="ENG-5")
        jql_search.put(epic, story)
        jira.on("GET", f"{API}/issue/5", epic)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[epic, story])

        commented = sec_issue("5", "ENG-5", kind="Epic", updated=ts(2))
        jql_search.put(commented)
        batches = len(db.record_batches)
        await sync(connector, jql_search, recent=[commented])

        assert not any("parent in" in q or "cf[" in q for q in jql_search.jql)
        assert {r.external_record_id for b in db.record_batches[batches:] for r in b} == {"5"}

        leveled = sec_issue("5", "ENG-5", kind="Epic", level=SECRET, updated=ts(3))
        jql_search.put(leveled)
        await sync(connector, jql_search, recent=[leveled])
        assert any("cf[" in q for q in jql_search.jql), "an epic that has a level now moves its stories"

    async def test_children_wait_in_the_checkpoint_until_the_whole_project_read_has_been_done(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        parent, child = sec_issue("2", "ENG-2"), sec_issue("3", "ENG-3", kind="Sub-task", parent="2")
        jql_search.put(parent, child)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[parent, child])
        parent_secured = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        child_secured = sec_issue("3", "ENG-3", level=SECRET, kind="Sub-task", parent="2")
        jql_search.put(parent_secured, child_secured)
        jql_search.refuse_children = True
        jql_search.fail_plain_from = 1  # and so does the read of every stored issue that replaces it

        await sync(connector, jql_search, recent=[parent_secured])

        assert not hidden_from_project(db.records["3"]), "the child could not be read yet"
        assert "2" in json.loads(store.values_for("project_ENG")["child_parents"]), "and is not forgotten"

        jql_search.refuse_children, jql_search.fail_plain_from = False, None
        await sync(connector, jql_search, recent=[])
        assert hidden_from_project(db.records["3"]) and readers(db, "3") == {("USER", "alice@example.com")}
        assert json.loads(store.values_for("project_ENG")["child_parents"]) == {}

    async def test_a_refused_children_jql_in_a_levels_pass_reads_the_project_before_the_digests_are_saved(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        epic, child = sec_issue("5", "ENG-5", kind="Epic", level=SECRET), sec_issue("6", "ENG-6", kind="Sub-task", parent="5", level=SECRET)
        jql_search.put(epic, child)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[epic, child])
        before = store.values_for("project_ENG")["security_levels"]

        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME]})  # no issue's `updated` moves
        jql_search.refuse_children = True
        jql_search.fail_plain_from = 2  # the levels pass is the first read of every stored issue; the fallback the second
        await sync(connector, jql_search, recent=[])

        assert store.values_for("project_ENG")["security_levels"] == before, "not saved before the fallback has read"
        assert "5" in json.loads(store.values_for("project_ENG")["child_parents"])

        jql_search.refuse_children, jql_search.fail_plain_from = False, None
        await sync(connector, jql_search, recent=[])
        assert store.values_for("project_ENG")["security_levels"] != before
        assert readers(db, "6") == {("USER", "alice@example.com"), ("USER", "carol@example.com")}
        assert json.loads(store.values_for("project_ENG")["child_parents"]) == {}

    async def test_a_refused_children_jql_in_a_levels_pass_is_made_up_in_the_same_sync_when_it_can(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        epic, child = sec_issue("5", "ENG-5", kind="Epic", level=SECRET), sec_issue("6", "ENG-6", kind="Sub-task", parent="5", level=SECRET)
        jql_search.put(epic, child)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[epic, child])
        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME]})
        jql_search.refuse_children = True

        await sync(connector, jql_search, recent=[])

        assert jql_search.plain_calls == 2, "the levels pass, then every stored issue"
        assert json.loads(store.values_for("project_ENG")["child_parents"]) == {}
        assert readers(db, "6") == {("USER", "alice@example.com"), ("USER", "carol@example.com")}

    async def test_a_server_that_refuses_the_jql_naming_children_has_the_whole_project_read(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        parent, child = sec_issue("2", "ENG-2"), sec_issue("3", "ENG-3", kind="Sub-task", parent="2")
        jql_search.put(parent, child)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[parent, child])
        parent_secured = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        child_secured = sec_issue("3", "ENG-3", level=SECRET, kind="Sub-task", parent="2")
        jql_search.put(parent_secured, child_secured)
        jql_search.refuse_children = True

        await sync(connector, jql_search, recent=[parent_secured])

        assert hidden_from_project(db.records["3"])


    async def test_a_story_in_a_project_with_no_scheme_leaves_a_leveled_epic_of_another_project(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)  # ENG has no scheme; the epic is in OTH, which has one
        jira.on("GET", f"{API}/issue/OTH-7", {"id": "700", "key": "OTH-7"})
        jira.on("GET", f"{API}/issue/700", {"id": "700", "key": "OTH-7", "fields": {
            "project": {"id": "20000", "key": "OTH"}, "security": {"id": SECRET}}})
        story = sec_issue("6", "ENG-6", kind="Story", epic="OTH-7")
        jql_search.put(story)
        connector, _ = await make_connector(db, store)

        await sync(connector, jql_search, recent=[story])

        # The people who read the story in ENG are not the epic's, so it hangs off its own project.
        assert db.records["6"].parent_external_record_id is None and not hidden_from_project(db.records["6"])


class TestReindex:
    def stored(
        self, connector: JiraDataCenterConnector, db: JiraRecordsDb, issue_id: str = "2", *, leveled: bool = False,
    ) -> TicketRecord:
        record = TicketRecord(
            access_rule=AccessRule.RESTRICTED if leveled else AccessRule.OPEN,
            id=f"t-{issue_id}", org_id="org-1", external_record_id=issue_id, record_name="[ENG-2] old",
            record_type=RecordType.TICKET, origin="CONNECTOR", connector_name="JIRA DATA CENTER",
            connector_id="jira-dc-1", external_record_group_id="10000", version=2, mime_type="text/plain",
            source_updated_at=connector._parse_jira_timestamp(ts(1)),
        )
        db.records[issue_id] = record
        return record

    async def test_a_reindex_applies_the_level_the_source_now_has(self, jira, db, store, jql_search) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        connector, _ = await make_connector(db, store)
        db.cached_app_users = [AppUser(
            app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="alice-key", org_id="org-1",
            email="alice@example.com", full_name="Alice", is_active=True,
        )]
        record = self.stored(connector, db)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))

        await connector.reindex_records([record])

        written = {r.external_record_id: r for batch in db.record_batches for r in batch}["2"]
        assert hidden_from_project(written) and readers(db, "2") == {("USER", "alice@example.com")}

    async def test_a_reindex_with_unreadable_levels_leaves_the_stored_issue_and_does_not_fail(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, members=json_response({"errorMessages": ["busy"]}, status=503))
        connector, _ = await make_connector(db, store)
        record = self.stored(connector, db)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))

        await connector.reindex_records([record])

        # Stored open and now carrying a level it cannot read: closed, not left readable by the project.
        written = {r.external_record_id: r for batch in db.record_batches for r in batch}["2"]
        assert hidden_from_project(written) and readers(db, "2") == set()
        assert db.reindexed == []

    async def test_an_issue_closed_by_a_reindex_is_opened_to_its_holders_by_the_next_clean_sync(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        plain = sec_issue("2", "ENG-2", updated=ts(1))
        jql_search.put(plain)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[plain])
        digests = json.loads(store.values_for("project_ENG")["security_levels"])
        assert not hidden_from_project(db.records["2"])

        # Jira gives the issue a level; the reindex sees it while the holders cannot be read.
        leveled = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        jql_search.put(leveled)
        jira.on("GET", f"{API}/issue/2", leveled)
        serve_scheme(jira, {SECRET: [ALICE]}, members=json_response({"errorMessages": ["busy"]}, status=503))
        await connector.reindex_records([db.records["2"]])
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        marked = json.loads(store.values_for("project_ENG")["security_levels"])
        assert marked == {**digests, "*": "retry"}

        # The levels read again with the digests unchanged: only the marker brings the issue back.
        serve_scheme(jira, {SECRET: [ALICE]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com")}
        assert "*" not in json.loads(store.values_for("project_ENG")["security_levels"])

    async def test_a_retry_marker_that_cannot_be_saved_does_not_cost_the_close(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, members=json_response({"errorMessages": ["busy"]}, status=503))
        connector, _ = await make_connector(db, store)
        record = self.stored(connector, db)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))
        connector._get_project_sync_checkpoint = AsyncMock(side_effect=RuntimeError("database unavailable"))

        await connector.reindex_records([record])

        written = {r.external_record_id: r for batch in db.record_batches for r in batch}["2"]
        assert hidden_from_project(written) and readers(db, "2") == set()

    async def test_a_stored_leveled_issue_keeps_its_grants_when_the_level_cannot_be_read(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, members=json_response({"errorMessages": ["busy"]}, status=503))
        connector, _ = await make_connector(db, store)
        record = self.stored(connector, db, leveled=True)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))

        await connector.reindex_records([record])

        assert db.record_batches == []  # nothing is written over the stored issue's grants
        assert [r.id for r in db.reindexed] == ["t-2"]
        assert store.values_for("project_ENG") is None  # and no retry is asked for

    async def test_a_reindex_does_not_narrow_the_audience_for_a_holder_it_could_not_resolve(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        # Carol is named by username only, and this process has not synced the users: nothing resolves her.
        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME]})
        connector, _ = await make_connector(db, store)
        db.cached_app_users = [AppUser(
            app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="alice-key", org_id="org-1",
            email="alice@example.com", full_name="Alice", is_active=True,
        )]
        record = self.stored(connector, db, leveled=True)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))

        await connector.reindex_records([record])

        assert db.record_batches == []  # not rewritten with alice alone

    async def test_a_stored_open_issue_that_now_has_a_level_with_an_unresolved_holder_is_closed(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME]})
        connector, _ = await make_connector(db, store)
        record = self.stored(connector, db)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))

        await connector.reindex_records([record])

        written = {r.external_record_id: r for batch in db.record_batches for r in batch}["2"]
        assert hidden_from_project(written) and readers(db, "2") == set()

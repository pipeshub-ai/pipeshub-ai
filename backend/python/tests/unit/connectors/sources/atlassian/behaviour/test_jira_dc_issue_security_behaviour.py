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
    LICENSED,
    IssueSearch,
    JiraRecordsDb,
    attachment,
    make_connector,
    stub_site,
    ts,
    user,
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
        elif "id in (" in jql:
            wanted = {w.strip() for w in jql.split("id in (")[1].split(")")[0].split(",")}
            rows = [i for i in self.source.values() if i["id"] in wanted]
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


def license_frank(jira: AtlassianApiStub) -> None:
    """frank has Jira product access too: a member of jira-software-users like everyone on the stub site."""
    real = next(r for m, path, r in jira._routes if (m, path) == ("GET", f"{API}/group/member"))

    def members(request: httpx.Request) -> httpx.Response:
        if AtlassianApiStub.query(request)["groupname"] == "jira-software-users":
            return json_response({"values": [{"key": k} for k in (*LICENSED, "frank-key")], "isLast": True})
        return real(request)

    jira.on("GET", f"{API}/group/member", members)


def readers(db: JiraRecordsDb, external_id: str) -> set[tuple[str, str]]:
    """Who the connector granted the issue to: (kind, email or id). A leveled issue's own grants and those of its
    level's record group, which holds the holders that name the same people on every issue. A RESTRICTED record
    needs a grant of its own, so the group's grants alone admit no one to it."""
    record = db.records.get(external_id)
    group = getattr(record, "access_group_external_id", None) if getattr(record, "access_rule", None) != AccessRule.RESTRICTED else None
    return {
        (str(p.entity_type.value if hasattr(p.entity_type, "value") else p.entity_type), p.email or p.external_id)
        for p in [*db.record_permissions.get(external_id, []), *(db.record_group_permissions.get(group, []) if group else [])]
    }


def hidden_from_project(record: TicketRecord) -> bool:
    """Read only by its own grants on top of the project, or through its level's record group (RESTRICTED under the
    project), never by the project alone."""
    return record.access_rule == AccessRule.RESTRICTED or bool(record.access_group_external_id)


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

    async def test_a_holder_outside_the_directory_is_not_granted_and_the_project_still_gates_the_issue(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {NOBODY: [holder("user", parameter="ghost", user={"name": "ghost", "emailAddress": "ghost@example.com"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=NOBODY))
        jql_search.recent = list(jql_search.source.values())
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        # ghost is not in the active-user list (deactivated or removed): Jira treats the account as gone, so the
        # level's grant does not name it. The issue still inherits the project and stays closed to it.
        assert db.records["2"].inherit_permissions is True
        assert hidden_from_project(db.records["2"])
        assert readers(db, "2") == set()


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

    @pytest.mark.parametrize("status", [404, 403, 401])
    async def test_level_holders_that_cannot_be_read_keep_a_stored_issues_grants(
        self, jira, db, store, jql_search, status,
    ) -> None:
        """The level-members route is not in Data Center's documented REST API (10.3 and 11.2 list only
        /issuesecurityschemes, /issuesecurityschemes/{id}, /project/{key}/issuesecuritylevelscheme,
        /project/{key}/securitylevel and /securitylevel/{id}, https://developer.atlassian.com/server/jira/platform/rest/v11002/),
        so any answer other than the members is a failed read: an unchanged issue keeps what it was granted."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        secured = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(secured)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[secured])
        assert readers(db, "2") == {("USER", "alice@example.com")}

        jira.on("GET", f"{API}/issuesecurityschemes/{SCHEME}/members", json_response({"message": "no"}, status=status))
        await sync(connector, jql_search, recent=[secured])

        assert readers(db, "2") == {("USER", "alice@example.com")}

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

    async def test_an_epic_changed_with_no_level_before_or_after_reads_its_stories_again_and_they_stay(
        self, jira, db, store, jql_search,
    ) -> None:
        """A leveled issue on its level's group is stored STRICT or OPEN, as one with no level is, so whether a changed
        epic had a level is not known: its stories are read again, and stay filed under it."""
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

        assert any("cf[" in q for q in jql_search.jql)
        written = {r.external_record_id for b in db.record_batches[batches:] for r in b}
        assert "6" in written and written <= {"5", "6"}
        assert db.records["6"].parent_external_record_id == "5" and not hidden_from_project(db.records["6"])

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
        # Not on its level's group before: the group's readers as the last sync read them are not known to read it now.
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        marked = json.loads(store.values_for("project_ENG")["security_levels"])
        assert marked == {**digests, "*": "retry"}

        # The levels read again with the digests unchanged: only the marker brings the issue back.
        serve_scheme(jira, {SECRET: [ALICE]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com")}
        assert "*" not in json.loads(store.values_for("project_ENG")["security_levels"])

    async def test_an_issue_moved_to_another_level_while_the_holders_cannot_be_read_is_closed_not_given_to_its_group(
        self, jira, db, store, jql_search,
    ) -> None:
        """ENG-2 is on TEAM's group (carol). Jira moves it to SECRET, whose group the last sync wrote with alice, and the
        reindex cannot read the holders (alice may have left SECRET since): closed until the next sync reads them, not
        opened to SECRET's group. ENG-3, already on SECRET's group, keeps it as stored."""
        stub_site(jira, jql_search)
        carol_level = holder("user", parameter="carol-key", user={"key": "carol-key"})
        serve_scheme(jira, {SECRET: [ALICE], TEAM: [carol_level]})
        on_team, on_secret = sec_issue("2", "ENG-2", level=TEAM), sec_issue("3", "ENG-3", level=SECRET)
        jql_search.put(on_team, on_secret)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[on_team, on_secret])
        assert readers(db, "2") == {("USER", "carol@example.com")} and readers(db, "3") == {("USER", "alice@example.com")}

        moved, edited = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)), sec_issue("3", "ENG-3", level=SECRET, updated=ts(2))
        jira.on("GET", f"{API}/issue/2", moved)
        jira.on("GET", f"{API}/issue/3", edited)
        serve_scheme(jira, {SECRET: [ALICE], TEAM: [carol_level]},
                     members=json_response({"errorMessages": ["busy"]}, status=503))
        db.record_batches.clear()
        await connector.reindex_records([db.records["2"], db.records["3"]])
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert "3" not in {r.external_record_id for batch in db.record_batches for r in batch}
        assert readers(db, "3") == {("USER", "alice@example.com")}

        jql_search.put(moved, edited)
        serve_scheme(jira, {SECRET: [ALICE], TEAM: [carol_level]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com")}

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


class TestProductAccessRegained:
    async def test_an_account_that_gains_product_access_gets_its_level_grants_without_the_issue_changing(
        self, jira, db, store, jql_search,
    ) -> None:
        """A level's user holder resolves only through the linked accounts, so the leveled issues are read again
        for an account newly linked, though none changed."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("user", parameter="carol-key", user={"key": "carol-key", "name": "carol"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="alice"))

        def product_group(*licensed: str) -> None:
            def members(request: httpx.Request) -> httpx.Response:
                name = AtlassianApiStub.query(request)["groupname"]
                keys = licensed if name == "jira-software-users" else ("alice-key", "carol-key")
                return json_response({"values": [{"key": k} for k in keys], "isLast": True})
            jira.on("GET", f"{API}/group/member", members)

        product_group("alice-key", "bob-key", "dave-key")
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert ("USER", "carol@example.com") not in readers(db, "2")

        db.cached_app_users = list(db.app_users)
        product_group(*LICENSED)
        batches_before = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "2")
        assert "2" in {r.external_record_id for batch in db.record_batches[batches_before:] for r in batch}

    async def test_a_project_a_key_filter_left_out_reads_its_levels_again_when_it_is_next_synced(
        self, jira, db, store, jql_search,
    ) -> None:
        """Carol is linked by a sync whose project-key filter leaves ENG out. ENG's leveled issues are read again
        when a later sync includes it, and only then: it still has the generation before carol's."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("user", parameter="carol-key", user={"key": "carol-key", "name": "carol"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="alice"))

        def product_group(*licensed: str) -> None:
            def members(request: httpx.Request) -> httpx.Response:
                name = AtlassianApiStub.query(request)["groupname"]
                keys = licensed if name == "jira-software-users" else ("alice-key", "carol-key")
                return json_response({"values": [{"key": k} for k in keys], "isLast": True})
            jira.on("GET", f"{API}/group/member", members)

        product_group("alice-key", "bob-key", "dave-key")
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert ("USER", "carol@example.com") not in readers(db, "2")

        product_group(*LICENSED)
        connector.config_service.config["filters"] = {
            "sync": {"values": {"project_keys": {"operator": "not_in", "type": "list", "value": ["ENG"]}}}}
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") not in readers(db, "2"), "ENG was not synced"

        connector.config_service.config["filters"] = {}
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "2")

        batches_before = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert "2" not in {r.external_record_id for batch in db.record_batches[batches_before:] for r in batch}, (
            "read again once"
        )
        assert store.values_for("project_ENG")["people_read"] == store.values_for("people_reread")["generation"], (
            "ENG covered carol's generation, so a later listing is a new one"
        )

    async def test_an_account_withdrawn_and_linked_again_gets_its_level_grants_again(self, jira, db, store, jql_search) -> None:
        """The first hand-out of carol ends once every project had her; linked again after a withdrawal, she starts
        a new one, so her level grant, dropped meanwhile, comes back though the issue did not change."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("user", parameter="carol-key", user={"key": "carol-key", "name": "carol"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="alice"))

        def product_group(*licensed: str) -> None:
            def members(request: httpx.Request) -> httpx.Response:
                name = AtlassianApiStub.query(request)["groupname"]
                keys = licensed if name == "jira-software-users" else ("alice-key", "carol-key")
                return json_response({"values": [{"key": k} for k in keys], "isLast": True})
            jira.on("GET", f"{API}/group/member", members)

        product_group("alice-key", "bob-key", "dave-key")
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        product_group(*LICENSED)
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "2")

        product_group("alice-key", "bob-key", "dave-key")
        await sync(connector, jql_search, recent=[])
        assert [u.source_user_id for u in db.withdrawn_users] == ["carol-key"]
        db.record_permissions["2"] = [p for p in db.record_permissions["2"] if p.email != "carol@example.com"]

        product_group(*LICENSED)
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "2")

    async def test_a_project_whose_generation_could_not_be_saved_reads_its_levels_again(
        self, jira, db, store, jql_search,
    ) -> None:
        """A failure saving the generation ENG covered leaves it owed: the next sync reads ENG's leveled issues again,
        rather than count carol's generation as covered."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("user", parameter="carol-key", user={"key": "carol-key", "name": "carol"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="alice"))

        def product_group(*licensed: str) -> None:
            def members(request: httpx.Request) -> httpx.Response:
                name = AtlassianApiStub.query(request)["groupname"]
                keys = licensed if name == "jira-software-users" else ("alice-key", "carol-key")
                return json_response({"values": [{"key": k} for k in keys], "isLast": True})
            jira.on("GET", f"{API}/group/member", members)

        product_group("alice-key", "bob-key", "dave-key")
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))

        product_group(*LICENSED)
        update = connector._update_project_sync_checkpoint

        async def failing_save(project_key: str, *args: object, **kwargs: object) -> None:
            if kwargs.get("people_read") is not None:
                raise RuntimeError("checkpoint store unavailable")
            await update(project_key, *args, **kwargs)

        connector._update_project_sync_checkpoint = failing_save
        with pytest.raises(RuntimeError, match="checkpoint store unavailable"):
            await sync(connector, jql_search, recent=[])

        connector._update_project_sync_checkpoint = update
        batches_before = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert "2" in {r.external_record_id for batch in db.record_batches[batches_before:] for r in batch}
        assert ("USER", "carol@example.com") in readers(db, "2")


class TestWithdrawnAccounts:
    async def test_a_deactivated_reporter_is_withdrawn_with_their_grants_and_regains_the_issue_when_listed_again(
        self, jira, db, store, jql_search,
    ) -> None:
        """Their level grant on the issue goes with the link (the store drops it); listed again, the leveled
        issues are read again, though none changed, so the grant comes back."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("reporter")]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol"))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert ("USER", "carol@example.com") in readers(db, "2")

        everyone_but_carol = [user("alice-key", "alice", "alice@example.com"), user("bob-key", "bob", "bob@example.com"),
                              user("dave-key", "dave", None)]
        jira.on("GET", f"{API}/user/list", json_response({"values": everyone_but_carol, "isLast": True}))
        await sync(connector, jql_search, recent=[])
        assert [u.source_user_id for u in db.withdrawn_users] == ["carol-key"]
        db.record_permissions["2"] = [p for p in db.record_permissions["2"] if p.email != "carol@example.com"]

        jira.on("GET", f"{API}/user/list", json_response({
            "values": [*everyone_but_carol, user("carol-key", "carol", "carol@example.com")], "isLast": True}))
        batches_before = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "2")
        assert "2" in {r.external_record_id for batch in db.record_batches[batches_before:] for r in batch}

    async def test_a_level_holder_a_short_user_list_missed_keeps_their_grant_or_has_the_issue_read_again(
        self, jira, db, store, jql_search,
    ) -> None:
        """carol is on the user list's second page, which fails. A level naming her by key alone still grants
        her (she is linked); one naming her by username alone cannot be resolved, so its issues are read again
        once the list is read in full, though neither issue changed."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("user", parameter="carol-key", user={"key": "carol-key"})],
                            TEAM: [CAROL_BY_NAME]})
        by_key, by_name = sec_issue("2", "ENG-2", level=SECRET), sec_issue("3", "ENG-3", level=TEAM)
        jql_search.put(by_key, by_name)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[by_key, by_name])
        assert ("USER", "carol@example.com") in readers(db, "2") and ("USER", "carol@example.com") in readers(db, "3")

        full_list = next(r for m, path, r in jira._routes if (m, path) == ("GET", f"{API}/user/list"))

        def first_page_only(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("cursor") == "abc":
                return json_response({"errorMessages": ["expired"]}, status=401)
            return full_list(request)

        jira.on("GET", f"{API}/user/list", first_page_only)
        changed = [sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)), sec_issue("3", "ENG-3", level=TEAM, updated=ts(2))]
        jql_search.put(*changed)
        await sync(connector, jql_search, recent=changed)
        assert connector._user_read_unfinished is True and db.withdrawn_users == []
        # Both are user holders, held by their level's group: the group naming carol by username keeps its stored grants.
        assert ("USER", "carol@example.com") in readers(db, "2") and ("USER", "carol@example.com") in readers(db, "3")

        jira.on("GET", f"{API}/user/list", full_list)
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "3")

    async def test_a_holder_the_level_dropped_loses_it_while_one_the_short_list_missed_keeps_it(
        self, jira, db, store, jql_search,
    ) -> None:
        """The level held alice and carol (by username). Jira takes alice out of it, and this sync's user list stops after
        its first page, which shows alice and not carol: carol cannot be resolved and keeps her grant (an account the list
        did not show keeps its last complete state), and alice, shown and no longer named, loses hers at once."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {TEAM: [ALICE, CAROL_BY_NAME]})
        leveled = sec_issue("3", "ENG-3", level=TEAM)
        jql_search.put(leveled)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[leveled])
        assert {("USER", "alice@example.com"), ("USER", "carol@example.com")} <= readers(db, "3")

        full_list = next(r for m, path, r in jira._routes if (m, path) == ("GET", f"{API}/user/list"))

        def first_page_only(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("cursor") == "abc":
                return json_response({"errorMessages": ["expired"]}, status=401)
            return full_list(request)

        jira.on("GET", f"{API}/user/list", first_page_only)
        serve_scheme(jira, {TEAM: [CAROL_BY_NAME]})
        await sync(connector, jql_search, recent=[])
        assert connector._user_read_unfinished is True
        assert ("USER", "carol@example.com") in readers(db, "3")
        assert ("USER", "alice@example.com") not in readers(db, "3")

        jira.on("GET", f"{API}/user/list", full_list)
        await sync(connector, jql_search, recent=[])
        assert {p for p in readers(db, "3") if p[0] == "USER"} == {("USER", "carol@example.com")}

    async def test_an_account_listed_again_stays_owed_a_re_read_until_the_stub_sweep_has_run(
        self, jira, db, store, jql_search,
    ) -> None:
        """carol is listed again and the leveled issues are read again, but the sweep that re-secures the project's
        stubs is skipped (the synced users could not be read): she stays owed, so the next sync reads and sweeps."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("reporter")]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol"))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        everyone = next(r for m, path, r in jira._routes if (m, path) == ("GET", f"{API}/user/list"))
        everyone_but_carol = [user("alice-key", "alice", "alice@example.com"), user("bob-key", "bob", "bob@example.com"),
                              user("dave-key", "dave", None)]
        jira.on("GET", f"{API}/user/list", json_response({"values": everyone_but_carol, "isLast": True}))
        await sync(connector, jql_search, recent=[])
        assert [u.source_user_id for u in db.withdrawn_users] == ["carol-key"]

        jira.on("GET", f"{API}/user/list", everyone)
        real_sweep = connector._sweep_placeholder_records
        connector._sweep_placeholder_records = AsyncMock(return_value=None)
        await sync(connector, jql_search, recent=[])
        assert json.loads(store.values_for("people_reread")["accounts"]) == ["carol-key"]

        connector._sweep_placeholder_records = AsyncMock(side_effect=real_sweep)
        await sync(connector, jql_search, recent=[])
        assert connector._sweep_placeholder_records.await_args.kwargs["full_sync_project_ids"] == {"10000"}
        assert json.loads(store.values_for("people_reread")["accounts"]) == []

    async def test_a_linked_account_with_a_hidden_email_reads_a_leveled_issue_synced_before_it_was_admitted(
        self, jira, db, store, jql_search,
    ) -> None:
        """frank is listed with a hidden email and is named by a level, but not linked, so ENG-2 is written without him.
        Once linked, the directory holds him by account, though ENG-2 has not changed: its level is read again, once,
        and only after a re-read that went through is he no longer owed one."""
        stub_site(jira, jql_search)
        license_frank(jira)
        everyone = [user("alice-key", "alice", "alice@example.com"), user("bob-key", "bob", "bob@example.com"),
                    user("carol-key", "carol", "carol@example.com"), user("frank-key", "frank", None)]
        jira.on("GET", f"{API}/user/list", json_response({"values": everyone, "isLast": True}))
        serve_scheme(jira, {SECRET: [holder("user", parameter="frank-key", user={"key": "frank-key", "name": "frank"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert ("USER", "frank-key") not in readers(db, "2")

        db.cached_app_users.append(AppUser(app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="frank-key",
                                           org_id="org-1", email="", full_name="Frank", is_active=True))
        jql_search.fail_plain_from = 1
        await sync(connector, jql_search, recent=[])
        # A user holder: the level's group, written before the walk, holds him whatever the issue's re-read.
        assert ("USER", "frank-key") in readers(db, "2")
        assert json.loads(store.values_for("people_reread")["accounts"]) == ["frank-key"]

        jql_search.fail_plain_from = None
        await sync(connector, jql_search, recent=[])
        assert ("USER", "frank-key") in readers(db, "2")
        assert json.loads(store.values_for("people_reread")["accounts"]) == []

        await sync(connector, jql_search, recent=[])
        assert jql_search.plain_calls == 0, "read again once, not every sync"

    async def test_a_project_the_sync_did_not_read_while_an_account_was_admitted_reads_its_levels_again_when_back(
        self, jira, db, store, jql_search,
    ) -> None:
        """frank is linked while ENG is missing from the project list (the sync account lost Browse, or it was
        archived). The re-read he is owed is handed on by the projects that were read; ENG keeps its checkpoint, so
        when it is listed again its leveled issues are read again for him."""
        stub_site(jira, jql_search)
        license_frank(jira)
        everyone = [user("alice-key", "alice", "alice@example.com"), user("frank-key", "frank", None)]
        jira.on("GET", f"{API}/user/list", json_response({"values": everyone, "isLast": True}))
        serve_scheme(jira, {SECRET: [holder("user", parameter="frank-key", user={"key": "frank-key", "name": "frank"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert ("USER", "frank-key") not in readers(db, "2")

        projects = next(r for m, path, r in jira._routes if (m, path) == ("GET", f"{API}/project"))
        jira.on("GET", f"{API}/project", json_response([]))
        db.cached_app_users.append(AppUser(app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="frank-key",
                                           org_id="org-1", email="", full_name="Frank", is_active=True))
        await sync(connector, jql_search, recent=[])
        assert json.loads(store.values_for("people_reread")["accounts"]) == []

        jira.on("GET", f"{API}/project", projects)
        await sync(connector, jql_search, recent=[])
        assert ("USER", "frank-key") in readers(db, "2")

    async def test_a_project_back_whose_stub_sweep_failed_is_read_and_swept_again(self, jira, db, store, jql_search) -> None:
        """ENG comes back after frank was admitted and its leveled issues are read again, but the stub sweep is skipped:
        nothing else owes ENG a re-read, so it keeps the older generation and is read and swept again next sync."""
        stub_site(jira, jql_search)
        license_frank(jira)
        jira.on("GET", f"{API}/user/list", json_response({"values": [
            user("alice-key", "alice", "alice@example.com"), user("frank-key", "frank", None)], "isLast": True}))
        serve_scheme(jira, {SECRET: [holder("user", parameter="frank-key", user={"key": "frank-key", "name": "frank"})]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        projects = next(r for m, path, r in jira._routes if (m, path) == ("GET", f"{API}/project"))
        jira.on("GET", f"{API}/project", json_response([]))
        db.cached_app_users.append(AppUser(app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="frank-key",
                                           org_id="org-1", email="", full_name="Frank", is_active=True))
        await sync(connector, jql_search, recent=[])

        jira.on("GET", f"{API}/project", projects)
        real_sweep = connector._sweep_placeholder_records
        connector._sweep_placeholder_records = AsyncMock(return_value=None)
        await sync(connector, jql_search, recent=[])
        connector._sweep_placeholder_records = AsyncMock(side_effect=real_sweep)
        await sync(connector, jql_search, recent=[])
        assert connector._sweep_placeholder_records.await_args.kwargs["full_sync_project_ids"] == {"10000"}

        await sync(connector, jql_search, recent=[])
        assert connector._sweep_placeholder_records.await_args.kwargs["full_sync_project_ids"] == set()

    async def test_an_issue_given_up_on_is_tried_again_once_for_a_newly_admitted_account(self, jira, db, store, jql_search) -> None:
        """ENG-2 was given up on (it kept failing, say on its parent's read) and has not changed since. frank is admitted:
        the walk for him tries it again rather than skipping it, so he reads it once it builds."""
        stub_site(jira, jql_search)
        license_frank(jira)
        jira.on("GET", f"{API}/user/list", json_response({"values": [
            user("alice-key", "alice", "alice@example.com"), user("frank-key", "frank", None)], "isLast": True}))
        serve_scheme(jira, {SECRET: [holder("user", parameter="frank-key", user={"key": "frank-key", "name": "frank"})]})
        leveled = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(leveled)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[leveled])
        checkpoint = store.values_for("project_ENG")
        checkpoint["given_up_issues"] = json.dumps({"2": leveled["fields"]["updated"]})

        db.cached_app_users.append(AppUser(app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="frank-key",
                                           org_id="org-1", email="", full_name="Frank", is_active=True))
        await sync(connector, jql_search, recent=[])
        assert ("USER", "frank-key") in readers(db, "2")

    @staticmethod
    async def _given_up_eng2_then_frank_listed(jira, db, store, jql_search) -> tuple[JiraDataCenterConnector, dict]:
        """ENG-2 stored, then given up on and unchanged since; frank is listed with a hidden email but not linked yet."""
        stub_site(jira, jql_search)
        license_frank(jira)
        jira.on("GET", f"{API}/user/list", json_response({"values": [
            user("alice-key", "alice", "alice@example.com"), user("frank-key", "frank", None)], "isLast": True}))
        serve_scheme(jira, {SECRET: [holder("user", parameter="frank-key", user={"key": "frank-key", "name": "frank"})]})
        leveled = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(leveled)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[leveled])
        store.values_for("project_ENG")["given_up_issues"] = json.dumps({"2": leveled["fields"]["updated"]})
        db.cached_app_users.append(AppUser(app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="frank-key",
                                           org_id="org-1", email="", full_name="Frank", is_active=True))
        return connector, leveled

    async def test_a_sync_that_cannot_walk_the_levels_leaves_the_given_up_retry_and_the_generation_owed(
        self, jira, db, store, jql_search,
    ) -> None:
        """frank is admitted while the level holders cannot be read, so no walk runs: neither the retry of ENG-2 nor
        the generation is spent. The next sync walks, tries ENG-2 again, and frank reads it."""
        connector, _ = await self._given_up_eng2_then_frank_listed(jira, db, store, jql_search)
        serve_scheme(jira, {SECRET: [holder("user", parameter="frank-key", user={"key": "frank-key", "name": "frank"})]},
                     members=json_response({"errorMessages": ["busy"]}, status=503))
        await sync(connector, jql_search, recent=[])
        serve_scheme(jira, {SECRET: [holder("user", parameter="frank-key", user={"key": "frank-key", "name": "frank"})]})
        await sync(connector, jql_search, recent=[])
        assert ("USER", "frank-key") in readers(db, "2")

    async def test_a_given_up_issue_that_fails_its_retry_once_is_tried_again_next_sync(self, jira, db, store, jql_search) -> None:
        """The retry of ENG-2 fails once (its parent's read, say). Its held attempt and the end of its give-up are saved
        with the retry, so the next walk tries it again rather than skipping it, and frank reads it."""
        connector, _ = await self._given_up_eng2_then_frank_listed(jira, db, store, jql_search)
        real = connector._extract_issue_data_with_parent
        failures = []

        async def fail_once(issue: dict[str, Any], users: dict[str, AppUser]) -> dict[str, Any]:
            if issue.get("id") == "2" and not failures:
                failures.append(1)
                raise RuntimeError("parent read failed")
            return await real(issue, users)

        connector._extract_issue_data_with_parent = fail_once
        await sync(connector, jql_search, recent=[])
        # frank is a user holder: the level's group, written before the walk, holds him whatever the issue's read.
        assert failures and ("USER", "frank-key") in readers(db, "2")
        assert "2" not in json.loads(store.values_for("project_ENG").get("given_up_issues") or "{}"), "tried again"
        batches = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert "2" in {r.external_record_id for b in db.record_batches[batches:] for r in b}, "built by the next sync"
        assert ("USER", "frank-key") in readers(db, "2")

    async def test_an_issue_given_up_on_that_still_fails_does_not_hold_a_level_pass(self, jira, db, store, jql_search) -> None:
        """ENG-2 was given up on and cannot be built. A level change reads it again (it may grant otherwise now), but its
        failure leaves it given up and does not hold the pass: the digests are saved and the next sync walks nothing."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        leveled = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(leveled)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[leveled])
        store.values_for("project_ENG")["given_up_issues"] = json.dumps({"2": leveled["fields"]["updated"]})
        real = connector._extract_issue_data_with_parent

        async def broken(issue: dict[str, Any], users: dict[str, AppUser]) -> dict[str, Any]:
            if issue.get("id") == "2":
                raise RuntimeError("cannot be built")
            return await real(issue, users)

        connector._extract_issue_data_with_parent = broken
        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME]})
        await sync(connector, jql_search, recent=[])
        assert jql_search.plain_calls >= 1, "the level pass read it"
        await sync(connector, jql_search, recent=[])
        assert jql_search.plain_calls == 0, "and is not repeated"

    async def test_an_issue_given_up_on_that_fails_a_pass_revoking_a_holder_is_closed(self, jira, db, store, jql_search) -> None:
        """ENG-2's level moves from alice to carol, but ENG-2, given up on, cannot be built: rather than keep alice's grant
        that Jira revoked, the stored issue is closed (no one reads it) until it can be built."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        leveled = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(leveled)
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=[leveled])
        assert readers(db, "2") == {("USER", "alice@example.com")}
        store.values_for("project_ENG")["given_up_issues"] = json.dumps({"2": leveled["fields"]["updated"]})
        real = connector._extract_issue_data_with_parent

        async def broken(issue: dict[str, Any], users: dict[str, AppUser]) -> dict[str, Any]:
            if issue.get("id") == "2":
                raise RuntimeError("cannot be built")
            return await real(issue, users)

        connector._extract_issue_data_with_parent = broken
        serve_scheme(jira, {SECRET: [CAROL_BY_NAME]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == set() and hidden_from_project(db.records["2"])

    async def test_a_given_up_issue_that_keeps_failing_ends_the_generation_walk_after_the_usual_attempts(
        self, jira, db, store, jql_search,
    ) -> None:
        """ENG-2 was given up on and cannot be built. The walk for frank revives it with a fresh attempt budget; once it is
        given up again, its failure no longer holds the walk, so the generation is covered and the walks stop."""
        connector, _ = await self._given_up_eng2_then_frank_listed(jira, db, store, jql_search)
        real = connector._extract_issue_data_with_parent

        async def broken(issue: dict[str, Any], users: dict[str, AppUser]) -> dict[str, Any]:
            if issue.get("id") == "2":
                raise RuntimeError("cannot be built")
            return await real(issue, users)

        connector._extract_issue_data_with_parent = broken
        for _ in range(8):
            await sync(connector, jql_search, recent=[])
            if jql_search.plain_calls == 0:
                break
        assert jql_search.plain_calls == 0, "the walks stopped"
        assert store.values_for("project_ENG")["people_read"] == store.values_for("people_reread")["generation"]

    async def test_a_short_user_list_that_shows_an_account_deactivated_withdraws_it(
        self, jira, db, store, jql_search,
    ) -> None:
        """Absence from a list cut short says nothing, but a row marked inactive does: carol is withdrawn with her
        grants, so neither the reindex nor the stub sweep, which read the linked accounts, can name her again."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [holder("reporter")]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol"))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert ("USER", "carol@example.com") in readers(db, "2")

        carol_inactive = {**user("carol-key", "carol", "carol@example.com"), "active": False}

        def short_list(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request).get("cursor") == "abc":
                return json_response({"errorMessages": ["expired"]}, status=401)
            return json_response({"values": [user("alice-key", "alice", "alice@example.com"), carol_inactive],
                                  "nextPage": f"{API}/user/list?cursor=abc", "isLast": False})

        jira.on("GET", f"{API}/user/list", short_list)
        await sync(connector, jql_search, recent=[])
        assert connector._user_read_unfinished is True
        assert [u.source_user_id for u in db.withdrawn_users] == ["carol-key"]
        assert "carol-key" not in {u.source_user_id for u in await db.get_all_app_users("jira-dc-1")}


def browse_reporter_and_lead(jira: AtlassianApiStub) -> None:
    """Browse names the project lead (alice) and each issue's reporter: a reporter reads only their own issues."""
    jira.on("GET", f"{API}/permissionscheme/7/permission", {"permissions": [
        {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead"}},
        {"permission": "BROWSE_PROJECTS", "holder": {"type": "reporter"}},
    ]})


def serve_devs(jira: AtlassianApiStub, members: list[str] | int) -> None:
    """The members of ``devs`` (a list of keys), or the status its member read fails with."""
    def answer(request: httpx.Request) -> httpx.Response:
        if AtlassianApiStub.query(request)["groupname"] == "jira-software-users":
            return json_response({"values": [{"key": k} for k in LICENSED], "isLast": True})
        if isinstance(members, int):
            return json_response({"errorMessages": ["busy"]}, status=members)
        return json_response({"values": [{"key": k} for k in members], "isLast": True})

    jira.on("GET", f"{API}/group/member", answer)


def serve_version(jira: AtlassianApiStub, version: tuple[int, ...]) -> None:
    """The server's version; before 10.3.13 and on 10.4 to 10.7 the users are listed by ``/user/search`` alone."""
    jira.on("GET", f"{API}/serverInfo", {"version": ".".join(map(str, version)), "versionNumbers": list(version)})
    everyone = [user("alice-key", "alice", "alice@example.com"), user("bob-key", "bob", "bob@example.com"),
                user("carol-key", "carol", "carol@example.com")]
    jira.on("GET", f"{API}/user/search", lambda request: json_response(
        everyone if AtlassianApiStub.query(request).get("username") == "." and AtlassianApiStub.query(request).get(
            "startAt", "0") == "0" else []))


def own_readers(db: JiraRecordsDb, external_id: str) -> set[str]:
    return {p.email or p.external_id for p in db.record_permissions.get(external_id, [])}


class TestABrowseNamedReporterInTheLevelThroughItsMembers:
    """Jira shows a leveled issue to someone the project's Browse names on it (its reporter) whom the level holds, here
    through a group: "Reporter" can hold Browse Projects, and a level's members may be "Groups"
    (https://confluence.atlassian.com/adminjiraserver/managing-project-permissions-938847145.html,
    https://confluence.atlassian.com/adminjiraserver/configuring-issue-level-security-938847117.html). carol reports
    ENG-2 and is in devs, which the level holds; bob reports ENG-3 and is not."""

    @staticmethod
    async def _site(
        jira: AtlassianApiStub, db: JiraRecordsDb, store: FakeCheckpointStore, jql_search: SecuredSearch,
        version: tuple[int, ...] = (11, 3, 13),
    ) -> JiraDataCenterConnector:
        stub_site(jira, jql_search)
        serve_version(jira, version)
        browse_reporter_and_lead(jira)
        serve_devs(jira, ["alice-key", "carol-key"])
        serve_scheme(jira, {SECRET: [DEVS]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol"),
                       sec_issue("3", "ENG-3", level=SECRET, reporter="bob"))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        return connector

    @pytest.mark.parametrize("version", [(10, 2, 0), (10, 7, 4), (11, 3, 13)], ids=["10.2", "10.7", "11.3"])
    async def test_the_reporter_in_the_levels_group_reads_it_on_every_supported_version(
        self, jira, db, store, jql_search, version,
    ) -> None:
        await self._site(jira, db, store, jql_search, version)
        assert (db.records["2"].access_rule, own_readers(db, "2")) == (AccessRule.OPEN, {"carol@example.com"})
        assert (db.records["3"].access_rule, own_readers(db, "3")) == (AccessRule.STRICT, set())
        assert db.records["2"].access_group_external_id == f"level:10000:{SECRET}"

    async def test_on_a_version_not_supported_no_one_new_is_linked_so_no_one_reads_it_alone(
        self, jira, db, store, jql_search,
    ) -> None:
        await self._site(jira, db, store, jql_search, (9, 12, 0))
        assert own_readers(db, "2") == set() and hidden_from_project(db.records["2"])

    async def test_out_of_the_group_the_reporter_loses_it_though_the_issue_did_not_change(
        self, jira, db, store, jql_search,
    ) -> None:
        connector = await self._site(jira, db, store, jql_search)
        serve_devs(jira, ["alice-key"])
        await sync(connector, jql_search, recent=[])
        assert (db.records["2"].access_rule, own_readers(db, "2")) == (AccessRule.STRICT, set())
        assert readers(db, "2") == {("GROUP", "devs")}

    async def test_a_group_whose_read_fails_grants_nobody_on_its_own_until_it_is_read(
        self, jira, db, store, jql_search,
    ) -> None:
        """Jira took carol out of devs, and this read of devs fails: the members the last read saw do not open the issue
        to her (the graph keeps the group's stored members). Read again, the issue is re-read once more."""
        connector = await self._site(jira, db, store, jql_search)
        serve_devs(jira, 503)
        await sync(connector, jql_search, recent=[])
        assert (db.records["2"].access_rule, own_readers(db, "2")) == (AccessRule.STRICT, set())

        serve_devs(jira, ["alice-key", "carol-key"])
        await sync(connector, jql_search, recent=[])
        assert (db.records["2"].access_rule, own_readers(db, "2")) == (AccessRule.OPEN, {"carol@example.com"})

    async def test_a_role_and_the_project_lead_hold_their_members_the_same_way(self, jira, db, store, jql_search) -> None:
        """carol is a Developers actor; alice leads ENG and reports ENG-4."""
        stub_site(jira, jql_search)
        browse_reporter_and_lead(jira)
        serve_devs(jira, ["alice-key", "carol-key"])
        serve_scheme(jira, {SECRET: [holder("projectRole", parameter="10002", projectRole={"id": "10002", "name": "Developers"})],
                            TEAM: [holder("projectLead")]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol"),
                       sec_issue("3", "ENG-3", level=SECRET, reporter="bob"),
                       sec_issue("4", "ENG-4", level=TEAM, reporter="alice"))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert own_readers(db, "2") == {"carol@example.com"} and own_readers(db, "3") == set()
        assert own_readers(db, "4") == {"alice@example.com"}

    async def _reindex_changed_eng2(self, connector, db, jira) -> TicketRecord:
        """ENG-2 changed at the source since the sync stored it, and is reindexed with no sync in between."""
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, reporter="carol", updated=ts(2)))
        db.record_batches.clear()
        await connector.reindex_records([db.records["2"]])
        return {r.external_record_id: r for batch in db.record_batches for r in batch}["2"]

    async def test_a_reindex_reads_the_group_again_so_a_reporter_jira_took_out_gets_no_grant(
        self, jira, db, store, jql_search,
    ) -> None:
        connector = await self._site(jira, db, store, jql_search)
        serve_devs(jira, ["alice-key"])
        written = await self._reindex_changed_eng2(connector, db, jira)
        assert (written.access_rule, own_readers(db, "2")) == (AccessRule.STRICT, set())
        assert "carol-key" in {u.source_user_id for u in connector._group_members["devs"]}, "the sync's map is left alone"

    async def test_a_reindex_opens_it_to_the_reporter_the_group_holds_now(self, jira, db, store, jql_search) -> None:
        connector = await self._site(jira, db, store, jql_search)
        written = await self._reindex_changed_eng2(connector, db, jira)
        assert (written.access_rule, own_readers(db, "2")) == (AccessRule.OPEN, {"carol@example.com"})
        assert written.source_updated_at == connector._parse_jira_timestamp(ts(2))
        # Opened through members read now, which the sync's level digests do not cover: the next sync reads it again.
        assert list(json.loads(store.values_for("project_ENG")["reindex_retry"])) == ["2"]

    async def test_a_reporter_a_reindex_found_in_the_group_who_leaves_it_before_the_next_sync_loses_it(
        self, jira, db, store, jql_search,
    ) -> None:
        """The sync read devs without carol; the reindex reads her in it and opens ENG-2 to her; Jira takes her out again
        before the next sync. That sync's digests match the ones it saved (carol out both times) and ENG-2 did not change,
        so only the reindex's retry entry brings it back, and carol loses it."""
        connector = await self._site(jira, db, store, jql_search)
        serve_devs(jira, ["alice-key"])
        await sync(connector, jql_search, recent=[])
        assert own_readers(db, "2") == set()

        serve_devs(jira, ["alice-key", "carol-key"])
        written = await self._reindex_changed_eng2(connector, db, jira)
        assert (written.access_rule, own_readers(db, "2")) == (AccessRule.OPEN, {"carol@example.com"})

        serve_devs(jira, ["alice-key"])
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol", updated=ts(2)))
        await sync(connector, jql_search, recent=[])
        assert (db.records["2"].access_rule, own_readers(db, "2")) == (AccessRule.STRICT, set())
        assert json.loads(store.values_for("project_ENG")["reindex_retry"]) == {}

    async def test_a_reindex_that_cannot_read_the_group_writes_no_grant_and_the_next_sync_reads_it_first(
        self, jira, db, store, jql_search,
    ) -> None:
        connector = await self._site(jira, db, store, jql_search)
        serve_devs(jira, 503)
        written = await self._reindex_changed_eng2(connector, db, jira)
        assert (written.access_rule, own_readers(db, "2")) == (AccessRule.STRICT, set())
        assert written.source_updated_at == connector._parse_jira_timestamp(ts(1)), "the stored timestamp stays"
        assert list(json.loads(store.values_for("project_ENG")["reindex_retry"])) == ["2"]

        # Not in the incremental search, and the level digests match: only the retry list brings it back.
        serve_devs(jira, ["alice-key", "carol-key"])
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol", updated=ts(2)))
        batches = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        assert any("id in (2)" in q for q in jql_search.jql)
        rebuilt = {r.external_record_id for b in db.record_batches[batches:] for r in b}
        assert "2" in rebuilt and "3" not in rebuilt, "the retry reads the queued issue alone"
        assert (db.records["2"].access_rule, own_readers(db, "2")) == (AccessRule.OPEN, {"carol@example.com"})
        assert json.loads(store.values_for("project_ENG")["reindex_retry"]) == {}

    async def test_a_reindex_of_a_level_holding_a_role_waits_for_the_sync(self, jira, db, store, jql_search) -> None:
        """A role's members are read by the sync only: the reindex grants no one through it and queues the issue."""
        connector = await self._site(jira, db, store, jql_search)
        serve_scheme(jira, {SECRET: [holder("projectRole", parameter="10002", projectRole={"id": "10002", "name": "Developers"})]})
        written = await self._reindex_changed_eng2(connector, db, jira)
        assert own_readers(db, "2") == set() and written.access_rule == AccessRule.STRICT
        assert list(json.loads(store.values_for("project_ENG")["reindex_retry"])) == ["2"]

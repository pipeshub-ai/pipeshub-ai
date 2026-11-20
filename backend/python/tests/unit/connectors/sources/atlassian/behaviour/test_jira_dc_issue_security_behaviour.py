"""Jira Data Center issue security levels, driven end to end over a fake Jira REST API.

An issue that has a security level is shown by Jira only to the holders of that level who
can also browse the project. The connector files such an issue as RESTRICTED and grants
it to the level's holders; an issue with no level is left to its project. Nothing about a
level moves an issue's ``updated``, so a level that gains a holder, a scheme that changes,
and every issue stored before levels were applied are read again from the checkpoint.
"""

import json
from collections.abc import Iterator
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
        elif "updated >=" in jql:
            rows = list(self.recent)
        else:
            self.plain_calls += 1
            if self.fail_plain_from is not None and self.plain_calls >= self.fail_plain_from:
                return json_response({"errorMessages": ["boom"]}, status=500)
            rows = list(self.source.values())
        return json_response({"issues": rows, "total": len(rows)})


@pytest.fixture
def jql_search() -> Iterator[SecuredSearch]:
    search = SecuredSearch()
    SEARCHES.append(search)
    yield search
    SEARCHES.remove(search)


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


# The people of the stub site: key -> (username, email). Jira lists only active users; "ghost" is none of them.
PEOPLE = {"alice-key": ("alice", "alice@example.com"), "bob-key": ("bob", "bob@example.com"),
          "carol-key": ("carol", "carol@example.com"), "dave-key": ("dave", None), "frank-key": ("frank", None)}
SEARCHES: list["SecuredSearch"] = []  # the issue search of the running test, for the viewers route to read issues from


def _answer(jira: AtlassianApiStub, path: str, **query: str) -> Any:  # noqa: ANN401
    response = jira(httpx.Request("GET", f"https://jira.example.com{path}", params=query))
    return response.json() if response.status_code == 200 else None


def _group_keys(jira: AtlassianApiStub, name: str) -> set[str]:
    keys: set[str] = set()
    start = 0
    while (page := _answer(jira, f"{API}/group/member", groupname=name, startAt=str(start))) is not None:
        keys |= {m.get("key") for m in page.get("values") or []}
        if page.get("isLast", True) or not page.get("values"):
            break
        start += len(page["values"])
    return keys


def _person(ref: object) -> str | None:
    """The key of a person named by key or username."""
    if not isinstance(ref, dict):
        return None
    key = ref.get("key") or next((k for k, (name, _) in PEOPLE.items() if name == ref.get("name")), None)
    return key if key in PEOPLE else None


def _issue_by_key(jira: AtlassianApiStub, key: str) -> dict[str, Any] | None:
    for search in SEARCHES:
        for issue in search.source.values():
            if issue.get("key") == key:
                return issue
    for method, path, response in jira._routes:
        if method == "GET" and path.startswith(f"{API}/issue/") and isinstance(response, dict) and response.get("key") == key:
            return response
    return None


def _level_members(jira: AtlassianApiStub, holders: list[dict[str, Any]], fields: dict[str, Any]) -> set[str]:
    """Who a level holds on an issue, as Jira expands its holders (users, groups, roles, the lead, issue roles)."""
    keys: set[str] = set()
    for h in holders:
        kind, param = h.get("type"), h.get("parameter")
        if kind == "user":
            keys.add(_person({"key": param}) or _person({"name": param}) or "")
        elif kind == "group":
            keys |= _group_keys(jira, param)
        elif kind == "projectRole":
            for actor in (_answer(jira, f"{API}/project/ENG/role/{param}") or {}).get("actors") or []:
                if actor.get("type") == "atlassian-group-role-actor":
                    keys |= _group_keys(jira, actor.get("name"))
                else:
                    keys.add(_person(actor) or "")
        elif kind == "projectLead":
            keys.add(_person(((_answer(jira, f"{API}/project") or [{}])[0]).get("lead")) or "")
        elif kind in ("reporter", "assignee"):
            keys.add(_person(fields.get(kind)) or "")
        elif kind == "applicationRole":
            for role in _answer(jira, f"{API}/applicationrole") or []:
                if not param or role.get("key") == param:
                    for group in role.get("groups") or []:
                        keys |= _group_keys(jira, group)
        elif kind == "userCustomField":
            value = fields.get(param)
            keys |= {_person(v) or "" for v in (value if isinstance(value, list) else [value])}
        elif kind == "groupCustomField":
            value = fields.get(param)
            for group in value if isinstance(value, list) else [value]:
                if isinstance(group, dict):
                    keys |= _group_keys(jira, group.get("name"))
        elif kind == "anyone":
            keys |= set(PEOPLE)
    return keys & set(PEOPLE)


def serve_scheme(
    jira: AtlassianApiStub, levels: dict[str, list[dict[str, Any]]], scheme: str = SCHEME, *, viewers: object = None,
) -> None:
    """The project's scheme and its levels, and, unless ``viewers`` replaces it, who may browse each issue
    (``GET /user/permission/search``): the people its level holds, as Jira expands its holders (Data Center lists no
    level's members). An issue with no level is not asked about."""
    jira.on("GET", f"{API}/project/ENG/issuesecuritylevelscheme", {
        "id": scheme, "name": "Scheme", "levels": [{"id": lid, "name": f"level {lid}"} for lid in levels],
    })

    def answer(request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        issue = _issue_by_key(jira, q.get("issueKey", ""))
        if issue is None or q.get("permissions") != "BROWSE_PROJECTS":
            return json_response({"errorMessages": ["no such issue"]}, status=404)
        fields = issue["fields"]
        level = (fields.get("security") or {}).get("id")
        keys = _level_members(jira, levels.get(level, []), fields) if level else set(PEOPLE)
        wanted = (q.get("username") or "").lower()
        rows = [{"key": k, "name": name, **({"emailAddress": email} if email else {}), "active": True}
                for k, (name, email) in sorted(PEOPLE.items()) if k in keys and (not wanted or any(
                    word.startswith(wanted) for word in [name, *(email or "").replace("@", " ").split()]))]
        start, size = int(q.get("startAt", 0)), int(q.get("maxResults", 50))
        return json_response(rows[start:min(start + size, 100)])

    jira.on("GET", f"{API}/user/permission/search", viewers if viewers is not None else answer)


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
    """Who the connector granted the issue to: (kind, email or id)."""
    return {
        (str(p.entity_type.value if hasattr(p.entity_type, "value") else p.entity_type), p.email or p.external_id)
        for p in db.record_permissions.get(external_id, [])
    }


def hidden_from_project(record: TicketRecord) -> bool:
    """Closed, or read only by its own grants (a leveled issue granted to the users Jira lists, inheriting nothing)."""
    return record.access_rule == AccessRule.RESTRICTED or record.inherit_permissions is False


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
        # Granted to exactly the people Jira lists as able to browse it, each by their own grant, inheriting nothing.
        assert db.records["2"].inherit_permissions is False
        assert readers(db, "2") == {
            ("USER", "alice@example.com"),            # a user holder by key, the assignee, devs, the lead
            ("USER", "carol@example.com"),            # a user holder named by username, devs, the role
            ("USER", "bob@example.com"),              # the reporter, the user field, the application role
            ("USER", "dave@example.com"),             # the application role's group
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
        # Its own viewers need not read its parent, so it is not filed under it.
        assert sub.parent_external_record_id is None and hidden_from_project(sub)
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

        # ghost is not an active user, so Jira lists no one: the issue is granted to no one and inherits nothing.
        assert db.records["2"].inherit_permissions is False
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
        assert json.loads(store.values_for("project_ENG")["security_levels"]) == {"*": "viewers"}

        # Who may browse a leveled issue can change with no sign in Jira's API, so each sync reads the leveled issues
        # again; the plain one is never touched.
        batches_after = len(db.record_batches)
        await sync(connector, jql_search, recent=[])
        written = {r.external_record_id for batch in db.record_batches[batches_after:] for r in batch}
        assert written == {"2"} and readers(db, "2") == {("USER", "alice@example.com")}

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
        assert readers(db, "2") == {("USER", "alice@example.com"), ("USER", "carol@example.com")}

        cleared = sec_issue("2", "ENG-2", updated=ts(4))
        jql_search.put(cleared)
        await sync(connector, jql_search, recent=[cleared])
        assert not hidden_from_project(db.records["2"]) and readers(db, "2") == set()

    async def test_a_level_that_gains_a_holder_is_applied_by_the_next_sync_though_no_issue_changed(
        self, jira, db, store, jql_search,
    ) -> None:
        """No documented route names a level's holders, so a holder added to a level shows nowhere: the next sync reads
        every leveled issue's viewers again, and leaves the plain one alone."""
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
        assert written == {"2", "3"}

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
        assert readers(db, "2") == {("USER", "alice@example.com"), ("USER", "carol@example.com")}

        # The scheme is taken off the project and the issue loses its level without a new `updated`.
        jql_search.put(sec_issue("2", "ENG-2"))
        no_scheme(jira)
        await sync(connector, jql_search, recent=[])
        assert not hidden_from_project(db.records["2"]) and readers(db, "2") == set()


class TestFailClosed:
    async def test_an_account_refused_the_viewers_hides_secured_issues_and_names_the_permission(
        self, jira, db, store, jql_search,
    ) -> None:
        """GET /user/permission/search "can be accessed by users with ADMINISTER_PROJECT permission for the project or
        global ADMIN or SYSADMIN rights" (403 otherwise): a leveled issue stays closed and the notice says what is missing.
        The level-members route Data Center does not have is never asked."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, viewers=json_response({"errorMessages": ["not admin"]}, status=403))
        jql_search.put(sec_issue("1", "ENG-1"), sec_issue("2", "ENG-2", level=SECRET))
        jql_search.recent = list(jql_search.source.values())
        connector, notes = await make_connector(db, store)

        await connector.run_sync()

        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert not hidden_from_project(db.records["1"])
        warning = next(n for n in notes.sent if "issue security levels" in n["title"])
        assert "Administer Projects" in warning["message"] and "ENG" in warning["message"]
        assert "does not list who holds" not in warning["message"]
        assert not jira.calls("GET", f"{API}/issuesecurityschemes/{SCHEME}/members")

    @pytest.mark.parametrize("status", [403, 404, 503])
    async def test_a_viewers_read_that_fails_closes_the_issue_until_one_passes(
        self, jira, db, store, jql_search, status,
    ) -> None:
        """Who may browse the issue is not known: no one keeps it (fail closed), and the next sync that reads it opens it
        to its viewers again, though the issue did not change."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]})
        secured = sec_issue("2", "ENG-2", level=SECRET)
        jql_search.put(secured)
        connector, notes = await make_connector(db, store)
        await sync(connector, jql_search, recent=[secured])
        assert readers(db, "2") == {("USER", "alice@example.com")}

        serve_scheme(jira, {SECRET: [ALICE]}, viewers=json_response({"message": "no"}, status=status))
        await sync(connector, jql_search, recent=[])
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        notices = [n for n in notes.sent if "issue security levels" in n["title"]]
        assert bool(notices) is (status == 403)

        serve_scheme(jira, {SECRET: [ALICE]})
        await sync(connector, jql_search, recent=[])
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

    async def test_a_transient_failure_reading_the_viewers_hides_the_stored_leveled_issues_until_it_passes(
        self, jira, db, store, jql_search,
    ) -> None:
        connector, notes = await self._stored(jira, db, store, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, viewers=json_response({"errorMessages": ["busy"]}, status=503))

        await sync(connector, jql_search, recent=[])

        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert not hidden_from_project(db.records["1"])
        assert json.loads(store.values_for("project_ENG")["security_levels"]) == {"*": "viewers"}

        serve_scheme(jira, {SECRET: [ALICE]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com")}

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
        assert store.values_for("project_ENG")["security_levels"] == digests, "the viewer grants are kept"
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

        # Everyone who reads the open story may not read the epic, so it hangs off the project; so does the story in
        # the epic's level, whose viewers Jira lists one issue at a time and need not read the epic.
        assert db.records["6"].parent_external_record_id is None and not hidden_from_project(db.records["6"])
        assert db.records["7"].parent_external_record_id is None and hidden_from_project(db.records["7"])


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
        """A leveled issue granted to its viewers is stored OPEN, as one with no level is, so whether a changed epic had a
        level is not known: its stories are read again, and stay filed under it."""
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
        serve_scheme(jira, {SECRET: [ALICE]}, viewers=json_response({"errorMessages": ["busy"]}, status=503))
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
        assert not hidden_from_project(db.records["2"])

        # Jira gives the issue a level; the reindex sees it while its viewers cannot be read.
        leveled = sec_issue("2", "ENG-2", level=SECRET, updated=ts(2))
        jql_search.put(leveled)
        jira.on("GET", f"{API}/issue/2", leveled)
        serve_scheme(jira, {SECRET: [ALICE]}, viewers=json_response({"errorMessages": ["busy"]}, status=503))
        await connector.reindex_records([db.records["2"]])
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()

        # The next sync reads every leveled issue's viewers again, though the issue did not change since.
        serve_scheme(jira, {SECRET: [ALICE]})
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com")}

    async def test_a_checkpoint_store_that_fails_does_not_cost_the_close(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, viewers=json_response({"errorMessages": ["busy"]}, status=503))
        connector, _ = await make_connector(db, store)
        record = self.stored(connector, db)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))
        connector._get_project_sync_checkpoint = AsyncMock(side_effect=RuntimeError("database unavailable"))

        await connector.reindex_records([record])

        written = {r.external_record_id: r for batch in db.record_batches for r in batch}["2"]
        assert hidden_from_project(written) and readers(db, "2") == set()

    async def test_a_stored_leveled_issue_is_closed_when_its_viewers_cannot_be_read(
        self, jira, db, store, jql_search,
    ) -> None:
        """Who may browse it now is not known: no stored grant is kept (it may name someone Jira took it from)."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE]}, viewers=json_response({"errorMessages": ["busy"]}, status=503))
        connector, _ = await make_connector(db, store)
        record = self.stored(connector, db, leveled=True)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))

        await connector.reindex_records([record])

        written = {r.external_record_id: r for batch in db.record_batches for r in batch}["2"]
        assert hidden_from_project(written) and readers(db, "2") == set()

    async def test_a_reindex_grants_the_viewers_this_process_knows_and_no_one_else(
        self, jira, db, store, jql_search,
    ) -> None:
        """Jira lists alice and carol; only alice is linked: the issue is granted to her alone, as a sync would."""
        stub_site(jira, jql_search)
        serve_scheme(jira, {SECRET: [ALICE, CAROL_BY_NAME]})
        connector, _ = await make_connector(db, store)
        db.cached_app_users = [AppUser(
            app_name="JIRA DATA CENTER", connector_id="jira-dc-1", source_user_id="alice-key", org_id="org-1",
            email="alice@example.com", full_name="Alice", is_active=True,
        )]
        record = self.stored(connector, db, leveled=True)
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, updated=ts(2)))

        await connector.reindex_records([record])

        assert readers(db, "2") == {("USER", "alice@example.com")} and db.records["2"].inherit_permissions is False

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

        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "2"), (
            "kept by the next sync"
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
        assert ("USER", "carol@example.com") in readers(db, "2")

        jira.on("GET", f"{API}/user/list", full_list)
        await sync(connector, jql_search, recent=[])
        assert ("USER", "carol@example.com") in readers(db, "3")

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
        assert ("USER", "frank-key") not in readers(db, "2"), "a failed re-read grants nothing"
        assert json.loads(store.values_for("people_reread")["accounts"]) == ["frank-key"]

        jql_search.fail_plain_from = None
        await sync(connector, jql_search, recent=[])
        assert ("USER", "frank-key") in readers(db, "2")
        assert json.loads(store.values_for("people_reread")["accounts"]) == []

        await sync(connector, jql_search, recent=[])
        assert jql_search.plain_calls == 1, "the per-sync read of the leveled issues, nothing more"

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
                     viewers=json_response({"errorMessages": ["busy"]}, status=503))
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
        assert failures and ("USER", "frank-key") not in readers(db, "2")
        await sync(connector, jql_search, recent=[])
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
        assert jql_search.plain_calls == 1, "only the per-sync read of the leveled issues"

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
            if store.values_for("project_ENG").get("people_read") == store.values_for("people_reread")["generation"]:
                break
        assert store.values_for("project_ENG")["people_read"] == store.values_for("people_reread")["generation"]
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set(), "closed while it cannot be built"

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


def serve_version(jira: AtlassianApiStub, version: tuple[int, ...]) -> None:
    """The server's version; before 10.3.13 and on 10.4 to 10.7 the users are listed by ``/user/search`` alone."""
    jira.on("GET", f"{API}/serverInfo", {"version": ".".join(map(str, version)), "versionNumbers": list(version)})
    everyone = [user(k, name, email) for k, (name, email) in PEOPLE.items() if k != "frank-key"]
    jira.on("GET", f"{API}/user/search", lambda request: json_response(
        everyone if AtlassianApiStub.query(request).get("username") == "." and AtlassianApiStub.query(request).get(
            "startAt", "0") == "0" else []))


def serve_devs(jira: AtlassianApiStub, members: list[str]) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if AtlassianApiStub.query(request)["groupname"] == "jira-software-users":
            return json_response({"values": [{"key": k} for k in LICENSED], "isLast": True})
        return json_response({"values": [{"key": k} for k in members], "isLast": True})

    jira.on("GET", f"{API}/group/member", answer)


class TestViewersPerVersion:
    """Data Center 10.2 to 11.3 document no route that lists a level's members, and document GET /user/permission/search
    (https://dac-static.atlassian.com/server/jira/platform/jira_software_dc_{10002,10007,11003}_swagger.v3.json): a leveled
    issue is granted to the users it lists, on the oldest, a middle and the newest supported version; on a version not
    supported it stays closed and the notice says why."""

    @pytest.mark.parametrize("version", [(10, 2, 0), (10, 7, 4), (11, 3, 13)], ids=["10.2", "10.7", "11.3"])
    async def test_a_leveled_issue_is_granted_to_its_viewers(self, jira, db, store, jql_search, version) -> None:
        stub_site(jira, jql_search)
        serve_version(jira, version)
        serve_devs(jira, ["alice-key", "carol-key"])
        serve_scheme(jira, {SECRET: [DEVS, holder("reporter")]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="bob"), sec_issue("1", "ENG-1"))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert readers(db, "2") == {("USER", "alice@example.com"), ("USER", "bob@example.com"), ("USER", "carol@example.com")}
        assert hidden_from_project(db.records["2"]) and not hidden_from_project(db.records["1"])
        asked = jira.calls("GET", f"{API}/user/permission/search")
        assert asked and {AtlassianApiStub.query(r)["permissions"] for r in asked} == {"BROWSE_PROJECTS"}

    async def test_on_a_version_not_supported_a_leveled_issue_stays_closed_and_the_notice_says_why(
        self, jira, db, store, jql_search,
    ) -> None:
        stub_site(jira, jql_search)
        serve_version(jira, (9, 12, 0))
        serve_scheme(jira, {SECRET: [ALICE]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET))
        connector, notes = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert hidden_from_project(db.records["2"]) and readers(db, "2") == set()
        assert not jira.calls("GET", f"{API}/user/permission/search")
        warning = next(n for n in notes.sent if "issue security levels" in n["title"])
        assert "version" in warning["message"] and "ENG" in warning["message"]

    async def test_a_reporter_a_reindex_found_among_the_viewers_who_leaves_before_the_next_sync_loses_it(
        self, jira, db, store, jql_search,
    ) -> None:
        """The sync reads devs without carol; the reindex finds carol among the viewers; Jira takes her out again
        before the next sync. Nothing about the issue or the groups the sync saw changed, but the next sync reads every
        leveled issue's viewers, so carol loses it."""
        stub_site(jira, jql_search)
        serve_devs(jira, ["alice-key"])
        serve_scheme(jira, {SECRET: [DEVS]})
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol"))
        connector, _ = await make_connector(db, store)
        await sync(connector, jql_search, recent=list(jql_search.source.values()))
        assert readers(db, "2") == {("USER", "alice@example.com")}

        serve_devs(jira, ["alice-key", "carol-key"])
        jira.on("GET", f"{API}/issue/2", sec_issue("2", "ENG-2", level=SECRET, reporter="carol", updated=ts(2)))
        await connector.reindex_records([db.records["2"]])
        assert readers(db, "2") == {("USER", "alice@example.com"), ("USER", "carol@example.com")}

        serve_devs(jira, ["alice-key"])
        jql_search.put(sec_issue("2", "ENG-2", level=SECRET, reporter="carol", updated=ts(2)))
        await sync(connector, jql_search, recent=[])
        assert readers(db, "2") == {("USER", "alice@example.com")}


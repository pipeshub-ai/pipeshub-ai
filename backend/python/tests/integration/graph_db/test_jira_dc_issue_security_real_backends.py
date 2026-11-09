"""Jira Data Center issue security levels against a real Neo4j and ArangoDB.

Requires the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml.
Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.

Jira shows an issue to a user who can browse its project and, when the issue has a security
level, is a member of that level. Each issue is judged by its own ``security`` field: Jira
copies a parent's level onto its sub-tasks, but not an epic's level onto the epic's stories,
which keep their own. A story is filed under its epic only when everyone who reads the story
reads the epic. Issues are built and written by the connector's own code, and every user's
access is read the three ways PipesHub reads it: the access check, search, and the Knowledge
Hub listing. Users are named by key, as Data Center names them.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
)
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.models.entities import AppUser, RecordGroup, RecordGroupType
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("it.jira.dc.issue_security")
ARANGO_DB = "jira_dc_issue_security_it"
PROJECT_ID, PROJECT_KEY = "10000", "PROJ"
EPIC_FIELD = "customfield_10100"

# Users: alice, bob and carol can browse the project; dave cannot. Data Center names them by key.
PEOPLE = {"alice": "alice-key", "bob": "bob-key", "carol": "carol-key", "dave": "dave-key"}
PROJECT_READERS = {"alice", "bob", "carol"}
LEVELS = {"10101": "alice", "10102": "carol", "10103": "dave"}  # level id -> its one member
# Granted to alice and to each issue's reporter, so two issues in it can have different readers.
REPORTER_LEVEL = "10104"


def _email(name: str, run: str) -> str:
    return f"{name}@{run}.example"


def _issue(issue_id: str, key: str, kind: str, *, level: str | None = None, parent: str | None = None,
           epic: str | None = None, reporter: str = "bob", updated: str = "2024-06-15T10:00:00.000+0000") -> dict:
    """A sub-task names its parent in ``parent``; a story names its epic in the Epic Link field."""
    fields = {
        "summary": key, "description": None, "status": {"name": "Open"}, "priority": {"name": "High"},
        "creator": {"key": PEOPLE["bob"], "name": "bob"}, "reporter": {"key": PEOPLE[reporter], "name": reporter},
        "assignee": None, "created": "2024-01-01T00:00:00.000+0000", "updated": updated,
        "issuetype": {"name": kind, "subtask": kind == "Sub-task"},
        "project": {"id": PROJECT_ID, "key": PROJECT_KEY},
        "attachment": [], "issuelinks": [],
        "security": {"id": level, "name": f"level {level}"} if level else None,
    }
    if parent:
        fields["parent"] = {"id": parent, "key": f"PROJ-{parent}"}
    if epic:
        fields[EPIC_FIELD] = {"id": epic, "key": f"PROJ-{epic}"}
    return {"id": issue_id, "key": key, "fields": fields}


# label -> (issue, who Jira shows it to). Parents come before their children.
ISSUES: dict[str, tuple[dict, set[str]]] = {
    "plain task": (_issue("1", "PROJ-1", "Task"), PROJECT_READERS),
    "leveled epic": (_issue("2", "PROJ-2", "Epic", level="10101"), {"alice"}),
    "unleveled story under the leveled epic": (_issue("3", "PROJ-3", "Story", epic="2"), PROJECT_READERS),
    "sub-task of that story": (_issue("4", "PROJ-4", "Sub-task", parent="3"), PROJECT_READERS),
    "story with another level under the leveled epic": (
        _issue("5", "PROJ-5", "Story", level="10102", epic="2"), {"carol"}),
    "unleveled epic": (_issue("6", "PROJ-6", "Epic"), PROJECT_READERS),
    "leveled story under the unleveled epic": (_issue("7", "PROJ-7", "Story", level="10101", epic="6"), {"alice"}),
    # Jira copies the parent's level onto a sub-task, so its own field carries it.
    "sub-task of the leveled story": (_issue("8", "PROJ-8", "Sub-task", level="10101", parent="7"), {"alice"}),
    "task leveled for someone outside the project": (_issue("9", "PROJ-9", "Task", level="10103"), set()),
    "unleveled story under the unleveled epic": (_issue("10", "PROJ-10", "Story", epic="6"), PROJECT_READERS),
    "sub-task of the unleveled story under the unleveled epic": (
        _issue("12", "PROJ-12", "Sub-task", parent="10"), PROJECT_READERS),
    "story with the epic's level under the leveled epic": (
        _issue("11", "PROJ-11", "Story", level="10101", epic="2"), {"alice"}),
    "epic leveled for alice and its reporter": (_issue("13", "PROJ-13", "Epic", level=REPORTER_LEVEL), {"alice", "bob"}),
    "story in that level, reported by carol": (
        _issue("14", "PROJ-14", "Story", level=REPORTER_LEVEL, epic="13", reporter="carol"), {"alice", "carol"}),
}

# Issue key -> the key of the issue it is filed under, None for its project.
FILED_UNDER: dict[str, str | None] = {
    "PROJ-1": None, "PROJ-2": None, "PROJ-3": None, "PROJ-4": "PROJ-3", "PROJ-5": None, "PROJ-6": None,
    "PROJ-7": "PROJ-6", "PROJ-8": "PROJ-7", "PROJ-9": None, "PROJ-10": "PROJ-6", "PROJ-12": "PROJ-10",
    "PROJ-11": "PROJ-2", "PROJ-13": None, "PROJ-14": None,
}


async def _remove(graph: IGraphDBProvider, org_id: str, connector_id: str, user_keys: list[str]) -> None:
    with contextlib.suppress(Exception):
        await graph.delete_connector_instance(connector_id, org_id)
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids DETACH DELETE n",
            parameters={"c": connector_id, "ids": [connector_id, org_id, *user_keys]},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                           else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id, connector_id = f"org-jiradc-{run}", f"jiradc-{run}"
        user_keys: list[str] = []
        cleanup.push_async_callback(_remove, graph, org_id, connector_id, user_keys)

        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "name": "Acme", "accountType": "enterprise", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.ORGS.value)
        assert await graph.batch_upsert_nodes(
            [{"id": connector_id, "orgId": org_id, "name": "Jira Data Center", "type": Connectors.JIRA.value,
              "appGroup": "Atlassian", "scope": "team", "isActive": True, "isConfigured": True,
              "isAuthenticated": True, "authType": "API_TOKEN", "createdBy": "it-admin",
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=CollectionNames.APPS.value)

        user_ids: dict[str, str] = {}
        for name in PEOPLE:
            key, user_id = f"{run}-{name}", f"user-{run}-{name}"
            user_keys.append(key)
            assert await graph.batch_upsert_nodes(
                [{"id": key, "userId": user_id, "orgId": org_id, "email": _email(name, run), "fullName": name,
                  "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
                collection=CollectionNames.USERS.value)
            assert await graph.batch_create_edges(
                [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": org_id,
                  "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION",
                  "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.BELONGS_TO.value)
            user_ids[name] = user_id

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages, **_: [True] * len(messages)
        config = MagicMock()
        config.get_config = AsyncMock(return_value=None)
        connector = JiraDataCenterConnector(
            logger, processor, GraphDataStore(logger, graph), config, connector_id, "team", "it-admin")
        connector.notify = AsyncMock()
        connector.data_source = MagicMock()  # the search itself is stood in for per test
        connector._epic_link_field_id = EPIC_FIELD
        connector._parent_link_field_id = ""
        yield {"graph": graph, "processor": processor, "connector": connector, "run": run,
               "org_id": org_id, "connector_id": connector_id, "user_ids": user_ids}


def _user_holder(name: str) -> dict[str, Any]:
    return {"type": "user", "parameter": PEOPLE[name], "user": {"key": PEOPLE[name], "name": name}}


def _context(**overrides: object) -> IssueSecurityContext:
    values: dict[str, Any] = {
        "knowledge": SchemeKnowledge.PRESENT, "scheme_id": "10050", "members_readable": True,
        "members_by_level": {
            **{level: [_user_holder(name)] for level, name in LEVELS.items()},
            REPORTER_LEVEL: [_user_holder("alice"), {"type": "reporter"}],
        },
    }
    return IssueSecurityContext(**{**values, **overrides})


def _users(world: dict[str, Any]) -> list[AppUser]:
    return [AppUser(app_name=Connectors.JIRA, connector_id=world["connector_id"], source_user_id=key,
                    org_id=world["org_id"], email=_email(name, world["run"]), full_name=name, is_active=True)
            for name, key in PEOPLE.items()]


def _project(world: dict[str, Any]) -> RecordGroup:
    return RecordGroup(
        id=str(uuid.uuid4()), org_id=world["org_id"], external_group_id=PROJECT_ID,
        connector_id=world["connector_id"], connector_name=Connectors.JIRA, name="Project", short_name=PROJECT_KEY,
        group_type=RecordGroupType.PROJECT,
    )


async def _open_project(world: dict[str, Any], context: IssueSecurityContext | None) -> None:
    """The project and its readers; ``context`` is what the connector knows of its scheme (None: levels unknown to it)."""
    run, connector, processor = world["run"], world["connector"], world["processor"]
    browse = [Permission(entity_type=EntityType.USER, email=_email(n, run), type=PermissionType.READ)
              for n in sorted(PROJECT_READERS)]
    await processor.on_new_record_groups([(_project(world), browse)])
    await processor.on_new_app_users(_users(world))
    if context is not None:
        connector._issue_security[PROJECT_ID] = context
    connector._get_issue_with_retry = AsyncMock(side_effect=_get_source_issue)


SOURCE: dict[str, dict] = {}  # the Jira being read: issue id -> its current payload


async def _get_source_issue(issue_id: str, _fields: list[str], *_: object, **__: object) -> MagicMock:
    issue = SOURCE.get(str(issue_id))
    return MagicMock(status=200 if issue else 404, json=MagicMock(return_value=issue or {}))


@pytest.fixture(autouse=True)
def _source() -> None:
    SOURCE.clear()
    SOURCE.update({issue["id"]: issue for issue, _ in ISSUES.values()})


async def _sync_project(world: dict[str, Any]) -> None:
    """Write the project and its issues the way a sync does, parents first."""
    await _open_project(world, _context())
    for issue, _ in ISSUES.values():
        await _write(world, [issue])


async def _write(world: dict[str, Any], issues: list[dict], *, again: bool = False) -> None:
    """Build and write issues as a sync does; ``again`` rebuilds unchanged ones, as a re-read does."""
    for issue in issues:
        SOURCE[issue["id"]] = issue
    connector = world["connector"]
    built = await connector._build_issue_records(issues, PROJECT_ID, _users(world), is_new_project=again)
    await connector._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})


def _edited(label: str, updated: str, **fields: object) -> dict:
    issue, _ = ISSUES[label]
    return {**issue, "fields": {**issue["fields"], "updated": updated, **fields}}


async def _ticket_ids(world: dict[str, Any]) -> dict[str, str]:
    docs = await world["graph"].get_nodes_by_filters(
        CollectionNames.RECORDS.value, {"connectorId": world["connector_id"]})
    return {d["recordName"].split("]")[0].lstrip("["): d.get("id") or d.get("_key")
            for d in docs if d.get("recordType") == "TICKET"}


async def _mismatches(world: dict[str, Any], readers: dict[str, set[str]], by_key: dict[str, str]) -> list[str]:
    """Where PipesHub's access check disagrees with ``readers`` (issue key -> who Jira shows it to)."""
    problems: list[str] = []
    for name in PEOPLE:
        allowed = set(await world["graph"].filter_accessible_record_ids(
            list(by_key.values()), world["user_ids"][name], world["org_id"]))
        for key, record_id in by_key.items():
            if (record_id in allowed) != (name in readers[key]):
                problems.append(f"{name} {'cannot' if name in readers[key] else 'can'} read {key}")
    return problems


async def _listed_under(world: dict[str, Any], name: str, record_id: str) -> set[str]:
    page = await KnowledgeHubService(logger, world["graph"]).get_nodes(
        user_id=world["user_ids"][name], org_id=world["org_id"], parent_id=record_id, parent_type="record", limit=200)
    assert page.success, page.error
    return {item.id for item in page.items}


async def _access(world: dict[str, Any], name: str, ids: list[str]) -> tuple[set[str], set[str], set[str]]:
    """Record ids the user may read by the access check, those search finds, and those listed."""
    graph, user_id, org_id = world["graph"], world["user_ids"][name], world["org_id"]
    checked = set(await graph.filter_accessible_record_ids(ids, user_id, org_id))
    # Search keeps the hits this check passes (the enumerated map is only a coarse scope).
    searched = await graph.filter_accessible_virtual_record_ids([f"vr-{i}" for i in ids], user_id, org_id)
    listed: set[str] = set()
    cursor = None
    for _ in range(20):
        page = await KnowledgeHubService(logger, graph).get_nodes(
            user_id=user_id, org_id=org_id, flattened=True, origins=["CONNECTOR"], limit=200, cursor=cursor)
        assert page.success, page.error
        listed |= {item.id for item in page.items}
        cursor = page.pagination.nextCursor if page.pagination else None
        if not cursor:
            break
    return checked, set(searched.values()) & set(ids), listed & set(ids)


def _readers() -> dict[str, set[str]]:
    return {issue["key"]: set(who) for issue, who in ISSUES.values()}


async def test_each_issue_is_read_by_its_project_and_its_own_level(world: dict[str, Any]) -> None:
    await _sync_project(world)
    graph = world["graph"]
    docs = await graph.get_nodes_by_filters(CollectionNames.RECORDS.value, {"connectorId": world["connector_id"]})
    by_key = {d["recordName"].split("]")[0].lstrip("["): d.get("id") or d.get("_key")
              for d in docs if d.get("recordType") == "TICKET"}
    for doc in docs:  # search returns indexed records only; stand in for the indexing service
        await graph.update_node(doc.get("id") or doc.get("_key"), CollectionNames.RECORDS.value, {
            "indexingStatus": "COMPLETED", "virtualRecordId": f"vr-{doc.get('id') or doc.get('_key')}"})
    ids = list(by_key.values())

    problems: list[str] = []
    for name in PEOPLE:
        checked, searched, listed = await _access(world, name, ids)
        for label, (issue, readers) in ISSUES.items():
            record_id = by_key[issue["key"]]
            allowed = name in readers
            for how, seen in (("read", checked), ("find in search", searched), ("browse to", listed)):
                if (record_id in seen) != allowed:
                    problems.append(f"{name} {'cannot' if allowed else 'can'} {how} the {label} ({issue['key']})")
    assert not problems, "PipesHub disagrees with Jira:\n  " + "\n  ".join(sorted(problems))


async def test_an_attachment_is_read_as_its_issue_is(world: dict[str, Any]) -> None:
    await _open_project(world, _context())
    secured = _edited("leveled epic", "2024-06-16T10:00:00.000+0000")
    secured["fields"]["attachment"] = [{"id": "200", "filename": "plan.pdf", "mimeType": "application/pdf",
                                        "size": 10, "created": "2024-06-15T09:00:00.000+0000"}]
    await _write(world, [secured])
    docs = await world["graph"].get_nodes_by_filters(
        CollectionNames.RECORDS.value, {"connectorId": world["connector_id"]})
    by_id = {d["externalRecordId"]: d.get("id") or d.get("_key") for d in docs}
    assert set(by_id) == {"2", "attachment_200"}

    problems = await _mismatches(
        world, {"PROJ-2": {"alice"}, "attachment": {"alice"}}, {"PROJ-2": by_id["2"], "attachment": by_id["attachment_200"]})
    assert not problems


async def test_a_story_is_filed_under_its_epic_only_when_its_readers_can_read_the_epic(world: dict[str, Any]) -> None:
    """Each reader sees a story under its epic, never a story they cannot read, and never
    loses one because it hangs under an epic they cannot read."""
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    readers = _readers()

    problems: list[str] = []
    for name in PEOPLE:
        for parent_key, parent_id in by_key.items():
            if name not in readers[parent_key]:
                continue
            listed = await _listed_under(world, name, parent_id)
            expected = {by_key[key] for key, under in FILED_UNDER.items() if under == parent_key and name in readers[key]}
            if listed & set(by_key.values()) != expected:
                problems.append(f"{name} under {parent_key}: listed {sorted(listed)}, expected {sorted(expected)}")
    assert not problems, "\n  ".join(problems)


async def test_levels_added_changed_and_removed_on_an_issue_take_effect(world: dict[str, Any]) -> None:
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    readers = _readers()
    label = "plain task"

    await _write(world, [_edited(label, "2024-06-16T10:00:00.000+0000", security={"id": "10102", "name": "x"})])
    assert not await _mismatches(world, {**readers, "PROJ-1": {"carol"}}, by_key)

    await _write(world, [_edited(label, "2024-06-17T10:00:00.000+0000", security={"id": "10101", "name": "x"})])
    assert not await _mismatches(world, {**readers, "PROJ-1": {"alice"}}, by_key)

    await _write(world, [_edited(label, "2024-06-18T10:00:00.000+0000", security=None)])
    assert not await _mismatches(world, readers, by_key)


async def test_stories_leave_an_epic_that_gains_a_level_and_return_when_it_loses_it(world: dict[str, Any]) -> None:
    """An epic's level moves no story's `updated`; the sync reads the epic's children again."""
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    readers = _readers()
    children = [ISSUES[label][0] for label in (
        "leveled story under the unleveled epic", "unleveled story under the unleveled epic")]

    await _write(world, [_edited("unleveled epic", "2024-06-16T10:00:00.000+0000",
                                 security={"id": "10102", "name": "level 10102"})])
    await _write(world, children, again=True)
    assert not await _mismatches(world, {**readers, "PROJ-6": {"carol"}}, by_key)
    assert not await _listed_under(world, "carol", by_key["PROJ-6"]) & {by_key["PROJ-7"], by_key["PROJ-10"]}

    await _write(world, [_edited("unleveled epic", "2024-06-17T10:00:00.000+0000", security=None)])
    await _write(world, children, again=True)
    assert not await _mismatches(world, readers, by_key)
    assert await _listed_under(world, "bob", by_key["PROJ-6"]) & set(by_key.values()) == {by_key["PROJ-10"]}


async def _stored_open(world: dict[str, Any]) -> dict[str, str]:
    """Every issue stored as the previous connector wrote it: open to the project, whatever its level."""
    await _open_project(world, None)  # no security context: the previous behaviour
    for issue, _ in ISSUES.values():
        await _write(world, [issue])
    by_key = await _ticket_ids(world)
    assert not await _mismatches(world, dict.fromkeys(by_key, PROJECT_READERS), by_key)
    return by_key


class _Source:
    """Answers the sync's issue search from ``SOURCE``: nothing for an incremental read, everything otherwise."""

    def __init__(self) -> None:
        self.jql: list[str] = []

    async def __call__(self, *, jql: str, **_: object) -> MagicMock:
        self.jql.append(jql)
        if "updated >=" in jql:
            rows = []
        elif "parent in (" in jql:
            wanted = {w.strip() for w in jql.split("parent in (")[1].split(")")[0].split(",")}
            rows = [i for i in SOURCE.values() if (i["fields"].get("parent") or {}).get("id") in wanted]
        elif "cf[" in jql:
            wanted = {w.strip() for w in jql.split("] in (")[1].split(")")[0].split(",")}
            rows = [i for i in SOURCE.values() if (i["fields"].get(EPIC_FIELD) or {}).get("key") in wanted]
        else:
            rows = list(SOURCE.values())
        return MagicMock(status=200, json=MagicMock(return_value={"issues": rows, "total": len(rows)}))


async def test_an_upgrade_closes_the_issues_stored_open_though_nothing_changed_at_the_source(world: dict[str, Any]) -> None:
    by_key = await _stored_open(world)
    connector = world["connector"]
    connector._load_issue_security = AsyncMock(return_value=_context())
    source = _Source()
    connector._search_issues_with_retry = source
    # The project synced before: a checkpoint, and no digest of its levels.
    await connector._update_project_sync_checkpoint(
        PROJECT_KEY, last_sync_time=get_epoch_timestamp_in_ms(), last_issue_updated=get_epoch_timestamp_in_ms() - 1000)
    users = _users(world)
    stats = {"new_count": 0, "updated_count": 0}

    await connector._sync_project_issues(_project(world), users, None)

    assert not await _mismatches(world, _readers(), by_key)
    assert (await connector._get_project_sync_checkpoint(PROJECT_KEY)).get("security_levels")

    # It is a one-time read: the next sync, with nothing changed, searches only for recent changes.
    source.jql.clear()
    await connector._sync_project_issues(_project(world), users, None)
    assert len(source.jql) == 1 and "updated >=" in source.jql[0], stats


async def test_an_upgrade_with_a_scheme_that_cannot_be_read_hides_the_leveled_issues_and_no_others(world: dict[str, Any]) -> None:
    by_key = await _stored_open(world)
    connector = world["connector"]
    connector._load_issue_security = AsyncMock(return_value=IssueSecurityContext(
        knowledge=SchemeKnowledge.UNKNOWN, read_status=403, scheme_forbidden=True))
    connector._search_issues_with_retry = _Source()
    await connector._update_project_sync_checkpoint(
        PROJECT_KEY, last_sync_time=get_epoch_timestamp_in_ms(), last_issue_updated=get_epoch_timestamp_in_ms() - 1000)

    await connector._sync_project_issues(_project(world), _users(world), None)

    leveled = {issue["key"] for issue, _ in ISSUES.values() if issue["fields"]["security"]}
    expected = {key: (set() if key in leveled else PROJECT_READERS) for key in by_key}
    assert not await _mismatches(world, expected, by_key)


@pytest.mark.parametrize("why", ["transient", "application_roles"])
async def test_an_upgrade_while_the_levels_are_unread_for_now_hides_the_leveled_issues_stored_open(
    world: dict[str, Any], why: str,
) -> None:
    """Issues stored open have no grants of their own to keep. A 429, 5xx or network error on the scheme or
    its holders, or application roles that cannot be read, must not leave them readable by the project."""
    by_key = await _stored_open(world)
    connector = world["connector"]
    if why == "transient":
        context = IssueSecurityContext(knowledge=SchemeKnowledge.UNKNOWN, defer_checkpoint=True, read_status=503)
    else:
        context = _context(members_by_level={"10101": [{"type": "applicationRole", "parameter": "jira-software"}]})
        connector._app_roles_unread = True
    connector._load_issue_security = AsyncMock(return_value=context)
    connector._search_issues_with_retry = _Source()
    await connector._update_project_sync_checkpoint(
        PROJECT_KEY, last_sync_time=get_epoch_timestamp_in_ms(), last_issue_updated=get_epoch_timestamp_in_ms() - 1000)

    await connector._sync_project_issues(_project(world), _users(world), None)

    leveled = {issue["key"] for issue, _ in ISSUES.values() if issue["fields"]["security"]}
    expected = {key: (set() if key in leveled else PROJECT_READERS) for key in by_key}
    assert not await _mismatches(world, expected, by_key)

    # Once the levels can be read, the holders get their issues.
    connector._app_roles_unread = False
    connector._load_issue_security = AsyncMock(return_value=_context())
    await connector._sync_project_issues(_project(world), _users(world), None)
    assert not await _mismatches(world, _readers(), by_key)


async def test_a_holder_keeps_a_secured_issue_when_its_level_cannot_be_read_during_a_full_sync(world: dict[str, Any]) -> None:
    """A full sync's first write of an existing record replaces its grants. A read that failed for now
    must not take the issue from the person who holds its level."""
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    connector, processor = world["connector"], world["processor"]
    down = _context(members_readable=False, members_by_level={}, defer_checkpoint=True, read_status=503)
    connector._issue_security[PROJECT_ID] = down
    run = processor.begin_full_sync_run(world["connector_id"])
    try:
        await _write(world, [ISSUES["leveled epic"][0]], again=True)  # unchanged at the source
    finally:
        processor.end_full_sync_run(world["connector_id"], run)

    # PROJ-2 stays alice's.
    assert not await _mismatches(world, _readers(), by_key)


async def test_an_issue_changed_while_its_level_cannot_be_read_does_not_keep_the_old_levels_holders(world: dict[str, Any]) -> None:
    """Its level may be another now, which Jira hides from the old holders."""
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    world["connector"]._issue_security[PROJECT_ID] = _context(
        members_readable=False, members_by_level={}, defer_checkpoint=True, read_status=503)

    await _write(world, [_edited("leveled epic", "2024-06-16T10:00:00.000+0000")])

    assert not await _mismatches(world, {**_readers(), "PROJ-2": set(), "PROJ-11": set()}, by_key)  # PROJ-11 hangs under it


async def test_an_issue_written_while_the_level_is_unreadable_is_hidden_until_it_can_be_read(world: dict[str, Any]) -> None:
    await _open_project(world, _context(members_readable=False, members_by_level={}, read_status=404))
    leveled = ISSUES["leveled epic"][0]
    plain = ISSUES["plain task"][0]
    await _write(world, [plain, leveled])
    by_key = await _ticket_ids(world)
    assert not await _mismatches(world, {"PROJ-1": PROJECT_READERS, "PROJ-2": set()}, by_key)

    world["connector"]._issue_security[PROJECT_ID] = _context()
    await _write(world, [leveled], again=True)
    assert not await _mismatches(world, {"PROJ-1": PROJECT_READERS, "PROJ-2": {"alice"}}, by_key)


async def _reindex(world: dict[str, Any], issue: dict) -> None:
    """Re-index the stored copy of ``issue`` against the source's current state of it."""
    connector = world["connector"]
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value=issue)))
    connector._get_fresh_datasource = AsyncMock(return_value=ds)
    connector.data_source = ds
    connector._load_issue_security = AsyncMock(return_value=connector._issue_security[PROJECT_ID])
    connector.indexing_filters = MagicMock()
    stored = await world["processor"].get_record_by_external_id(
        connector_id=world["connector_id"], external_record_id=issue["id"])
    await connector.reindex_records([stored])


async def test_a_reindex_applies_the_level_the_source_now_has(world: dict[str, Any]) -> None:
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    await _reindex(world, _edited("plain task", "2024-07-01T10:00:00.000+0000", security={"id": "10102", "name": "x"}))
    assert not await _mismatches(world, {**_readers(), "PROJ-1": {"carol"}}, by_key)


async def test_a_reindex_with_an_unreadable_scheme_strips_nobody(world: dict[str, Any]) -> None:
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    world["connector"]._issue_security[PROJECT_ID] = _context(
        members_readable=False, members_by_level={}, defer_checkpoint=True, read_status=503)
    await _reindex(world, _edited("leveled epic", "2024-07-01T10:00:00.000+0000"))
    assert not await _mismatches(world, _readers(), by_key)


async def test_a_reindex_closes_a_stored_open_issue_that_now_has_a_level_it_cannot_read(world: dict[str, Any]) -> None:
    """The project must not keep reading what Jira now hides, while the holders cannot be read."""
    by_key = await _stored_open(world)
    world["connector"]._issue_security[PROJECT_ID] = _context(
        members_readable=False, members_by_level={}, defer_checkpoint=True, read_status=503)

    await _reindex(world, _edited("plain task", "2024-07-01T10:00:00.000+0000", security={"id": "10102", "name": "x"}))

    expected = dict.fromkeys(by_key, PROJECT_READERS) | {"PROJ-1": set()}
    assert not await _mismatches(world, expected, by_key)


async def test_a_project_without_a_scheme_is_read_by_the_same_people_open_or_strict(world: dict[str, Any]) -> None:
    """Issues of a project with no scheme are written STRICT with a rewrite now; before, OPEN. The project's
    readers, and nobody else, read every issue of the hierarchy either way."""
    plain = [{**issue, "fields": {**issue["fields"], "security": None}} for issue, _ in ISSUES.values()]
    await _open_project(world, None)  # OPEN, as before
    for issue in plain:
        await _write(world, [issue])
    by_key = await _ticket_ids(world)

    async def readers() -> dict[str, set[str]]:
        return {name: set(await world["graph"].filter_accessible_record_ids(
            list(by_key.values()), world["user_ids"][name], world["org_id"])) for name in PEOPLE}

    before = await readers()
    world["connector"]._issue_security[PROJECT_ID] = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
    for issue in plain:
        await _write(world, [{**issue, "fields": {**issue["fields"], "updated": "2024-08-01T10:00:00.000+0000"}}], again=True)
    after = await readers()

    assert after == before
    assert all(after[name] == set(by_key.values()) for name in PROJECT_READERS) and after["dave"] == set()

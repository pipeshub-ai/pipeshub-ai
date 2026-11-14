"""Jira issue security levels against a real Neo4j and ArangoDB.

Requires the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml.
Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.

Jira shows an issue to a user who can browse its project and, when the issue has a
security level, is a member of that level. Each issue is judged by its own ``security``
field: Jira copies a parent's level onto its sub-tasks, but not an epic's level onto
the epic's children, which keep their own (often none). A story is filed under its epic
only when everyone who reads the story reads the epic. Issues are built and written by
the connector's own code, and every user's access is read the three ways PipesHub reads
it: the access check, search, and the Knowledge Hub listing.
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
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
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

logger = logging.getLogger("it.jira.issue_security")
ARANGO_DB = "jira_issue_security_it"
PROJECT_ID, PROJECT_KEY = "10000", "PROJ"

# Users: alice, bob and carol can browse the project; dave cannot.
PEOPLE = {"alice": "acc-alice", "bob": "acc-bob", "carol": "acc-carol", "dave": "acc-dave"}
PROJECT_READERS = {"alice", "bob", "carol"}
LEVELS = {"10101": "alice", "10102": "carol", "10103": "dave"}  # level id -> its one member
# Granted to alice and to each issue's reporter, so two issues in it can have different readers.
REPORTER_LEVEL = "10104"


def _email(name: str, run: str) -> str:
    return f"{name}@{run}.example"


def _issue(issue_id: str, key: str, kind: str, *, level: str | None = None, parent: str | None = None,
           reporter: str = "bob") -> dict:
    hierarchy = {"Epic": 1, "Story": 0, "Task": 0, "Sub-task": -1}[kind]
    return {"id": issue_id, "key": key, "fields": {
        "summary": key, "description": None, "status": {"name": "Open"}, "priority": {"name": "High"},
        "creator": {"accountId": PEOPLE["bob"]}, "reporter": {"accountId": PEOPLE[reporter]}, "assignee": None,
        "created": "2024-01-01T00:00:00.000+0000", "updated": "2024-06-15T10:00:00.000+0000",
        "issuetype": {"name": kind, "hierarchyLevel": hierarchy, "subtask": kind == "Sub-task"},
        "project": {"id": PROJECT_ID, "key": PROJECT_KEY},
        "parent": {"id": parent, "key": f"PROJ-{parent}"} if parent else None,
        "attachment": [], "issuelinks": [],
        "security": {"id": level, "name": f"level {level}"} if level else None,
    }}


# key -> (issue, who Jira shows it to). Parents come before their children.
ISSUES: dict[str, tuple[dict, set[str]]] = {
    "plain task": (_issue("1", "PROJ-1", "Task"), PROJECT_READERS),
    "leveled epic": (_issue("2", "PROJ-2", "Epic", level="10101"), {"alice"}),
    "unleveled story under the leveled epic": (_issue("3", "PROJ-3", "Story", parent="2"), PROJECT_READERS),
    "sub-task of that story": (_issue("4", "PROJ-4", "Sub-task", parent="3"), PROJECT_READERS),
    "story with another level under the leveled epic": (
        _issue("5", "PROJ-5", "Story", level="10102", parent="2"), {"carol"}),
    "unleveled epic": (_issue("6", "PROJ-6", "Epic"), PROJECT_READERS),
    "leveled story under the unleveled epic": (_issue("7", "PROJ-7", "Story", level="10101", parent="6"), {"alice"}),
    # Jira copies the parent's level onto a sub-task, so its own field carries it.
    "sub-task of the leveled story": (_issue("8", "PROJ-8", "Sub-task", level="10101", parent="7"), {"alice"}),
    "task leveled for someone outside the project": (_issue("9", "PROJ-9", "Task", level="10103"), set()),
    "unleveled story under the unleveled epic": (_issue("10", "PROJ-10", "Story", parent="6"), PROJECT_READERS),
    "sub-task of the unleveled story under the unleveled epic": (
        _issue("12", "PROJ-12", "Sub-task", parent="10"), PROJECT_READERS),
    "story with the epic's level under the leveled epic": (
        _issue("11", "PROJ-11", "Story", level="10101", parent="2"), {"alice"}),
    "epic leveled for alice and its reporter": (_issue("13", "PROJ-13", "Epic", level=REPORTER_LEVEL), {"alice", "bob"}),
    "story in that level, reported by carol": (
        _issue("14", "PROJ-14", "Story", level=REPORTER_LEVEL, parent="13", reporter="carol"), {"alice", "carol"}),
}

# Issue key -> the key of the issue it is filed under, None for its project. A story goes under
# its epic only when everyone who reads the story reads the epic; a sub-task always does.
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
            parameters={"c": connector_id, "ids": [connector_id, org_id, f"all_{org_id}", *user_keys]},
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
        org_id, connector_id = f"org-jira-{run}", f"jira-{run}"
        user_keys: list[str] = []
        cleanup.push_async_callback(_remove, graph, org_id, connector_id, user_keys)

        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "name": "Acme", "accountType": "enterprise", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.ORGS.value)
        assert await graph.batch_upsert_nodes(
            [{"id": connector_id, "orgId": org_id, "name": "Jira", "type": Connectors.JIRA.value, "appGroup": "Atlassian",
              "scope": "team", "isActive": True, "isConfigured": True, "isAuthenticated": True,
              "authType": "OAUTH", "createdBy": "it-admin", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
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
        connector = JiraConnector(logger, processor, GraphDataStore(logger, graph), config, connector_id, "team", "it-admin")
        connector.notify = AsyncMock()
        yield {"graph": graph, "processor": processor, "connector": connector, "run": run,
               "org_id": org_id, "connector_id": connector_id, "user_ids": user_ids, "user_keys": user_keys}


async def _sync_project(world: dict[str, Any]) -> None:
    """Write the project and its issues the way a sync does, parents first."""
    run, connector, processor = world["run"], world["connector"], world["processor"]
    project = RecordGroup(
        id=str(uuid.uuid4()), org_id=world["org_id"], external_group_id=PROJECT_ID,
        connector_id=world["connector_id"], connector_name=Connectors.JIRA, name="Project", short_name=PROJECT_KEY,
        group_type=RecordGroupType.PROJECT,
    )
    browse = [Permission(entity_type=EntityType.USER, email=_email(n, run), type=PermissionType.READ)
              for n in sorted(PROJECT_READERS)]
    await processor.on_new_record_groups([(project, browse)])

    users = [AppUser(app_name=Connectors.JIRA, connector_id=world["connector_id"], source_user_id=acc,
                     org_id=world["org_id"], email=_email(name, run), full_name=name, is_active=True)
             for name, acc in PEOPLE.items()]
    await processor.on_new_app_users(users)
    connector._issue_security[PROJECT_ID] = IssueSecurityContext(
        knowledge=SchemeKnowledge.PRESENT, scheme_id="10050", members_readable=True,
        members_by_level={
            **{level: [_user_holder(name)] for level, name in LEVELS.items()},
            REPORTER_LEVEL: [_user_holder("alice"), {"type": "reporter"}],
        },
    )
    for issue, _ in ISSUES.values():
        await _write(world, [issue])


def _user_holder(name: str) -> dict[str, Any]:
    return {"type": "user", "parameter": PEOPLE[name], "user": {"accountId": PEOPLE[name]}}


def _users(world: dict[str, Any]) -> list[AppUser]:
    return [AppUser(app_name=Connectors.JIRA, connector_id=world["connector_id"], source_user_id=acc,
                    org_id=world["org_id"], email=_email(name, world["run"]), full_name=name, is_active=True)
            for name, acc in PEOPLE.items()]


async def _write(world: dict[str, Any], issues: list[dict], *, again: bool = False,
                 users: list[AppUser] | None = None) -> None:
    """Build and write issues as a sync does; ``again`` rebuilds unchanged ones, as a re-read does.
    ``users`` is the directory the sync read, everyone by default."""
    connector = world["connector"]
    built, _ = await connector._build_issue_records(
        issues, PROJECT_ID, _users(world) if users is None else users, is_new_project=again)
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


async def test_a_story_stored_under_its_epic_moves_to_the_project_when_read_again(world: dict[str, Any]) -> None:
    """Earlier syncs filed a story under its epic, so it needed the epic's level too."""
    await _sync_project(world)
    connector, run = world["connector"], world["run"]
    users = [AppUser(app_name=Connectors.JIRA, connector_id=world["connector_id"], source_user_id=acc,
                     org_id=world["org_id"], email=_email(name, run), full_name=name, is_active=True)
             for name, acc in PEOPLE.items()]
    story, _ = ISSUES["unleveled story under the leveled epic"]
    old = {**story, "fields": {**story["fields"], "updated": "2024-06-16T10:00:00.000+0000"}}
    built, _ = await connector._build_issue_records([old], PROJECT_ID, users)
    built[0][0].parent_external_record_id = "2"  # as the earlier connector wrote it
    await connector._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})

    graph, record_ids = world["graph"], None
    docs = await graph.get_nodes_by_filters(CollectionNames.RECORDS.value, {"connectorId": world["connector_id"]})
    record_ids = [d.get("id") or d.get("_key") for d in docs if d.get("externalRecordId") == "3"]
    assert record_ids
    assert not await graph.filter_accessible_record_ids(record_ids, world["user_ids"]["bob"], world["org_id"])

    again = {**story, "fields": {**story["fields"], "updated": "2024-06-17T10:00:00.000+0000"}}
    built, _ = await connector._build_issue_records([again], PROJECT_ID, users)
    await connector._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})
    assert await graph.filter_accessible_record_ids(record_ids, world["user_ids"]["bob"], world["org_id"]) == set(record_ids)


async def test_a_story_is_filed_under_its_epic_only_when_its_readers_can_read_the_epic(world: dict[str, Any]) -> None:
    """Each reader sees a story under its epic, never a story they cannot read, and never
    loses one because it hangs under an epic they cannot read."""
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: who for issue, who in ISSUES.values()}

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


async def test_stories_leave_an_epic_that_gains_a_level_and_return_when_it_loses_it(world: dict[str, Any]) -> None:
    """An epic's level moves no story's `updated`; the sync reads the epic's children again."""
    await _sync_project(world)
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
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


async def test_a_story_stored_under_its_project_moves_under_its_epic_when_read_again(world: dict[str, Any]) -> None:
    """The previous rule filed every story under its project; the placement pass re-reads it."""
    await _sync_project(world)
    connector = world["connector"]
    story, _ = ISSUES["unleveled story under the unleveled epic"]
    built, _ = await connector._build_issue_records(
        [_edited("unleveled story under the unleveled epic", "2024-06-16T10:00:00.000+0000")], PROJECT_ID,
        _users(world))
    built[0][0].parent_external_record_id = None  # as the previous connector wrote it
    built[0][0].parent_record_type = None
    await connector._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})
    by_key = await _ticket_ids(world)
    assert by_key["PROJ-10"] not in await _listed_under(world, "bob", by_key["PROJ-6"])

    await _write(world, [story], again=True)
    readers = {issue["key"]: who for issue, who in ISSUES.values()}
    assert not await _mismatches(world, readers, by_key)
    assert by_key["PROJ-10"] in await _listed_under(world, "bob", by_key["PROJ-6"])


async def _reindex(world: dict[str, Any], issue: dict, *, context: IssueSecurityContext | None = None) -> None:
    """Re-index the stored copy of ``issue`` against the source's current state of it. With
    ``context``, the project's security and Browse holders are read as a fresh read reads them."""
    connector = world["connector"]
    ds = MagicMock()
    ds.get_issue = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value=issue)))
    parents = [i for i, _ in ISSUES.values() if i["id"] == (issue["fields"]["parent"] or {}).get("id")]
    ds.bulk_fetch_issues = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value={"issues": parents})))
    grants = [{"permission": "BROWSE_PROJECTS", "holder": h} for h in (context.browse_holders or [])] if context else []
    ds.get_assigned_permission_scheme = AsyncMock(return_value=MagicMock(status=200, json=MagicMock(return_value={"id": 7})))
    ds.get_permission_scheme_grants = AsyncMock(
        return_value=MagicMock(status=200, json=MagicMock(return_value={"permissions": grants})))
    connector._get_fresh_datasource = AsyncMock(return_value=ds)
    connector.data_source = ds
    connector._load_issue_security = AsyncMock(return_value=context or connector._issue_security[PROJECT_ID])
    connector.indexing_filters = MagicMock()
    stored = await world["processor"].get_record_by_external_id(
        connector_id=world["connector_id"], external_record_id=issue["id"])
    await connector.reindex_records([stored])


@pytest.mark.parametrize("level", ["10101", None], ids=["parent_level", "no_level"])
@pytest.mark.parametrize("parent_has_key", [True, False])
async def test_a_story_that_became_a_sub_task_is_read_as_one_after_a_reindex(
    world: dict[str, Any], parent_has_key: bool, level: str | None,
) -> None:
    """Its access follows its leveled parent, not the project's browsers the story was read as.

    Jira sends a sub-task its parent's level; the payload without one is kept too, since only the
    parent edge then keeps the sub-task from being read as project-open."""
    await _sync_project(world)
    await _write(world, [_issue("20", "PROJ-20", "Story", parent="7")])
    sub = _issue("20", "PROJ-20", "Sub-task", level=level, parent="7")
    sub["fields"]["updated"] = "2024-07-01T10:00:00.000+0000"
    if not parent_has_key:
        sub["fields"]["parent"] = {"id": "7"}
    await _reindex(world, sub)
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: who for issue, who in ISSUES.values()} | {"PROJ-20": {"alice"}}
    assert not await _mismatches(world, readers, by_key)
    assert by_key["PROJ-20"] in await _listed_under(world, "alice", by_key["PROJ-7"])


async def test_a_sub_task_that_became_a_story_is_read_as_one_after_a_reindex(world: dict[str, Any]) -> None:
    await _sync_project(world)
    await _write(world, [_issue("21", "PROJ-21", "Sub-task", level="10101", parent="7")])
    story = _issue("21", "PROJ-21", "Story")
    story["fields"]["updated"] = "2024-07-01T10:00:00.000+0000"
    await _reindex(world, story)
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: who for issue, who in ISSUES.values()} | {"PROJ-21": PROJECT_READERS}
    assert not await _mismatches(world, readers, by_key)
    assert by_key["PROJ-21"] not in await _listed_under(world, "alice", by_key["PROJ-7"])


async def test_a_reporter_outside_the_project_reads_only_their_own_issue_and_the_story_leaves_its_epic(
    world: dict[str, Any],
) -> None:
    """Browse held by the reporter: a direct grant on the top-level issue (and its files), no more.

    The story is then not filed under its epic, so the old parent edge must go; when the project
    drops the holder it goes back, and the reporter loses it."""
    await _sync_project(world)
    context = world["connector"]._issue_security[PROJECT_ID]
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
    label = "unleveled story under the unleveled epic"
    assert by_key["PROJ-10"] in await _listed_under(world, "bob", by_key["PROJ-6"])

    context.browse_holders = [{"type": "reporter"}]
    story = _edited(label, "2024-06-16T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]})
    await _write(world, [story], again=True)
    # dave reads PROJ-10 alone: not its sub-task PROJ-12, which follows it and needs the project.
    assert not await _mismatches(world, {**readers, "PROJ-10": PROJECT_READERS | {"dave"}}, by_key)
    for name in ("alice", "bob", "carol"):
        assert by_key["PROJ-10"] not in await _listed_under(world, name, by_key["PROJ-6"])

    context.browse_holders = []
    await _write(world, [_edited(label, "2024-06-17T10:00:00.000+0000")], again=True)
    assert not await _mismatches(world, readers, by_key)
    assert by_key["PROJ-10"] in await _listed_under(world, "bob", by_key["PROJ-6"])


async def test_an_unread_scheme_keeps_the_reporter_of_a_stored_issue_and_the_story_where_it_was_filed(
    world: dict[str, Any],
) -> None:
    await _sync_project(world)
    context = world["connector"]._issue_security[PROJECT_ID]
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
    label = "unleveled story under the unleveled epic"
    context.browse_holders = [{"type": "reporter"}]
    await _write(world, [_edited(label, "2024-06-16T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]})],
                 again=True)

    # The scheme could not be read, and the project has no issue security scheme.
    world["connector"]._issue_security[PROJECT_ID] = IssueSecurityContext(
        knowledge=SchemeKnowledge.NONE, browse_holders=None)
    kept = _edited(label, "2024-06-17T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]})
    del kept["fields"]["security"]  # a payload that does not return the level: the stored grants stay
    await _write(world, [kept])
    assert not await _mismatches(world, {**readers, "PROJ-10": PROJECT_READERS | {"dave"}}, by_key)


async def test_a_level_cleared_while_the_scheme_is_unread_takes_the_issue_from_its_old_level_members(
    world: dict[str, Any],
) -> None:
    """dave held the level but cannot browse the project; OPEN with his stored grant would still let him in."""
    await _sync_project(world)
    context = world["connector"]._issue_security[PROJECT_ID]
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
    context.browse_holders = None
    await _write(world, [_edited("task leveled for someone outside the project", "2024-06-17T10:00:00.000+0000",
                                 security=None)])
    assert not await _mismatches(world, {**readers, "PROJ-9": PROJECT_READERS}, by_key)


async def test_reassigning_an_issue_moves_the_assignee_grant_on_the_stored_record(world: dict[str, Any]) -> None:
    """Browse held by the assignee: an incremental write replaces the old assignee's grant."""
    await _sync_project(world)
    world["connector"]._issue_security[PROJECT_ID].browse_holders = [{"type": "assignee"}]
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
    for stamp, assignee, who in (
        ("2024-06-16", "dave", PROJECT_READERS | {"dave"}),   # dave gains it
        ("2024-06-17", "alice", PROJECT_READERS),             # reassigned: dave loses it
        ("2024-06-18", "dave", PROJECT_READERS | {"dave"}),   # and back
    ):
        await _write(world, [_edited("plain task", f"{stamp}T10:00:00.000+0000", assignee={"accountId": PEOPLE[assignee]})])
        assert not await _mismatches(world, {**readers, "PROJ-1": who}, by_key)


async def test_a_withdrawn_reporter_reads_nothing_of_theirs_through_the_org_team_before_and_after_a_resync(
    world: dict[str, Any],
) -> None:
    """dave reads PROJ-1 as its reporter and holds a role. His Jira account is deactivated: the withdrawal takes his
    grants with the link, so the org's All team passing the gate (as a public project makes it) opens nothing of
    his, and a re-sync that no longer lists him does not grant him the issue again. Listed again, a re-read does."""
    await _sync_project(world)
    graph, connector, processor = world["graph"], world["connector"], world["processor"]
    org_id, connector_id, run = world["org_id"], world["connector_id"], world["run"]
    connector._issue_security[PROJECT_ID].browse_holders = [{"type": "reporter"}]
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
    task = _edited("plain task", "2024-06-16T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]})
    await _write(world, [task])
    now = get_epoch_timestamp_in_ms()
    role = f"{run}-role"
    assert await graph.batch_upsert_nodes([{"id": role, "orgId": org_id, "connectorId": connector_id, "name": "Developers",
                                            "externalRoleId": f"{PROJECT_KEY}_10002", "connectorName": Connectors.JIRA.value,
                                            "createdAtTimestamp": now, "updatedAtTimestamp": now}],
                                          collection=CollectionNames.ROLES.value)
    dave_key = f"{run}-dave"
    assert await graph.batch_create_edges(
        [{"from_id": dave_key, "from_collection": CollectionNames.USERS.value, "to_id": role,
          "to_collection": CollectionNames.ROLES.value, "type": "USER", "role": "READER",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.PERMISSION.value)
    assert not await _mismatches(world, {**readers, "PROJ-1": PROJECT_READERS | {"dave"}}, by_key)

    for key in world["user_keys"]:
        await graph.add_user_to_all_team(org_id, key)
    await processor.ensure_team_app_edge(connector_id)
    connector._stored_app_users = await processor.get_all_app_users(connector_id)
    listed = [u for u in _users(world) if u.source_user_id != PEOPLE["dave"]]
    await connector._withdraw_ineligible_app_users(listed, {PEOPLE["dave"]})
    assert not await _mismatches(world, readers, by_key)
    held = {str(e.get("_to") or e.get("to_id") or "") for e in await graph.get_edges_from_node(
        f"{CollectionNames.USERS.value}/{dave_key}", CollectionNames.PERMISSION.value)}
    assert not any(role in t or by_key["PROJ-1"] in t for t in held), held  # his role and his grant went too

    await _write(world, [_edited("plain task", "2024-06-17T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]})],
                 users=listed)
    assert not await _mismatches(world, readers, by_key)

    await processor.on_new_app_users(_users(world))  # reactivated
    await _write(world, [_edited("plain task", "2024-06-18T10:00:00.000+0000", reporter={"accountId": PEOPLE["dave"]})])
    assert not await _mismatches(world, {**readers, "PROJ-1": PROJECT_READERS | {"dave"}}, by_key)


@pytest.mark.parametrize("read_in_full", [True, False], ids=["read_in_full", "read_cut_short"])
async def test_a_reindex_does_not_grant_a_withdrawn_reporter_whose_email_is_in_the_payload(
    world: dict[str, Any], read_in_full: bool,
) -> None:
    """The reindex resolves the issue's own people from its payload too; the grants it writes, and those a sync's
    rewrite writes, must still name only the synced directory (with the linked accounts when the user list was not
    read in full), or a withdrawn reporter named there with an email would get the issue back."""
    await _sync_project(world)
    connector, processor = world["connector"], world["processor"]
    context = connector._issue_security[PROJECT_ID]
    context.browse_holders = [{"type": "reporter"}]
    by_key = await _ticket_ids(world)
    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
    dave = {"accountId": PEOPLE["dave"], "emailAddress": _email("dave", world["run"]), "displayName": "dave"}
    await _write(world, [_edited("plain task", "2024-06-16T10:00:00.000+0000", reporter=dave)])
    assert not await _mismatches(world, {**readers, "PROJ-1": PROJECT_READERS | {"dave"}}, by_key)

    for key in world["user_keys"]:  # the org's All team passes the gate (as a public project makes it)
        await world["graph"].add_user_to_all_team(world["org_id"], key)
    await processor.ensure_team_app_edge(world["connector_id"])
    connector._stored_app_users = await processor.get_all_app_users(world["connector_id"])
    listed = [u for u in _users(world) if u.source_user_id != PEOPLE["dave"]]
    connector._listed_account_ids = {u.source_user_id for u in listed}
    await connector._withdraw_ineligible_app_users(listed, {PEOPLE["dave"]})
    assert not await _mismatches(world, readers, by_key)

    # The next sync starts from the links left; its user list may be cut short.
    connector._stored_app_users = await processor.get_all_app_users(world["connector_id"])
    connector._user_read_unfinished = not read_in_full
    await _write(world, [_edited("plain task", "2024-06-17T10:00:00.000+0000", reporter=dave)], users=listed)
    assert not await _mismatches(world, readers, by_key)

    await _reindex(world, _edited("plain task", "2024-06-18T10:00:00.000+0000", reporter=dave), context=context)
    assert not await _mismatches(world, readers, by_key)


async def _org_member(world: dict[str, Any], name: str) -> str:
    """A PipesHub user of the org with no Jira account; returns its user id."""
    graph, run, org_id = world["graph"], world["run"], world["org_id"]
    now = get_epoch_timestamp_in_ms()
    key, user_id = f"{run}-{name}", f"user-{run}-{name}"
    world["user_keys"].append(key)
    assert await graph.batch_upsert_nodes(
        [{"id": key, "userId": user_id, "orgId": org_id, "email": _email(name, run), "fullName": name,
          "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.USERS.value)
    assert await graph.batch_create_edges(
        [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": org_id,
          "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.BELONGS_TO.value)
    return user_id


async def test_a_public_project_is_read_by_the_whole_org_and_no_longer_once_it_is_private(world: dict[str, Any]) -> None:
    """Browse held by `anyone` (Jira's anonymous access): erin, a PipesHub user with no Jira account, and dave, who
    cannot browse the project, read its issues with no level; a leveled one stays with its level. frank has no
    Jira account but a grant left on dave's leveled issue: opening the gate to the org removes it first. When the
    project is private again, the org's All team stops passing the gate and erin reads nothing."""
    await _sync_project(world)
    graph, run, org_id, connector = world["graph"], world["run"], world["org_id"], world["connector"]
    erin_id = await _org_member(world, "erin")
    frank_id = await _org_member(world, "frank")
    by_key = await _ticket_ids(world)
    now = get_epoch_timestamp_in_ms()
    assert await graph.batch_create_edges(
        [{"from_id": f"{run}-frank", "from_collection": CollectionNames.USERS.value, "to_id": by_key["PROJ-9"],
          "to_collection": CollectionNames.RECORDS.value, "type": "USER", "role": "READER",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.PERMISSION.value)
    for key in world["user_keys"]:  # the platform puts every user in the org's All team
        await graph.add_user_to_all_team(org_id, key)

    readers = {issue["key"]: set(who) for issue, who in ISSUES.values()}
    project = RecordGroup(
        id=str(uuid.uuid4()), org_id=org_id, external_group_id=PROJECT_ID, connector_id=world["connector_id"],
        connector_name=Connectors.JIRA, name="Project", short_name=PROJECT_KEY, group_type=RecordGroupType.PROJECT)
    browse = [Permission(entity_type=EntityType.USER, email=_email(n, run), type=PermissionType.READ)
              for n in sorted(PROJECT_READERS)]
    anyone = Permission(entity_type=EntityType.ORG, external_id="anyone_authenticated", type=PermissionType.READ)
    unleveled = {by_key[key] for key, who in readers.items() if who == PROJECT_READERS}

    async def reads(user_id: str) -> set[str]:
        return set(await graph.filter_accessible_record_ids(list(by_key.values()), user_id, org_id))

    # dave can now browse the project, so he also reads the issue in his own level.
    public = {key: who | {"dave"} if who == PROJECT_READERS else who for key, who in readers.items()} | {"PROJ-9": {"dave"}}
    await world["processor"].on_new_record_groups([(project, [*browse, anyone])])
    await connector._admit_everyone_to_public_projects([(project, [*browse, anyone])], written_in_full=True)
    assert not await _mismatches(world, public, by_key)
    assert await reads(erin_id) == unleveled
    assert await reads(frank_id) == unleveled  # not PROJ-9: his stale grant went before the gate opened

    await world["processor"].on_new_record_groups([(project, browse)])
    await connector._admit_everyone_to_public_projects([(project, browse)], written_in_full=True)
    assert not await _mismatches(world, readers, by_key)
    assert await reads(erin_id) == set()

"""Jira issue security levels against a real Neo4j and ArangoDB.

Requires the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml.
Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.

Jira shows an issue to a user who can browse its project and, when the issue has a
security level, is a member of that level. Each issue is judged by its own ``security``
field: Jira copies a parent's level onto its sub-tasks, but not an epic's level onto
the epic's children, which keep their own (often none). Issues are built and written
by the connector's own code, and every user's access is read the three ways PipesHub
reads it: the access check, search, and the Knowledge Hub listing.
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


def _email(name: str, run: str) -> str:
    return f"{name}@{run}.example"


def _issue(issue_id: str, key: str, kind: str, *, level: str | None = None, parent: str | None = None) -> dict:
    hierarchy = {"Epic": 1, "Story": 0, "Task": 0, "Sub-task": -1}[kind]
    return {"id": issue_id, "key": key, "fields": {
        "summary": key, "description": None, "status": {"name": "Open"}, "priority": {"name": "High"},
        "creator": {"accountId": PEOPLE["bob"]}, "reporter": {"accountId": PEOPLE["bob"]}, "assignee": None,
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
               "org_id": org_id, "connector_id": connector_id, "user_ids": user_ids}


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
        members_by_level={level: [{"type": "user", "parameter": PEOPLE[name], "user": {"accountId": PEOPLE[name]}}]
                          for level, name in LEVELS.items()},
    )
    for issue, _ in ISSUES.values():
        built, _ = await connector._build_issue_records([issue], PROJECT_ID, users)
        await connector._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})


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

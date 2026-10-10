"""Drive Workspace access on the graph the fixed connector writes, on both backends.

Each shape is the one ``tests/unit/connectors/sources/google/test_drive_team_qa_acl_shapes.py``
derives from the live QA ACLs (Drive Workspace 05388601, 2026-10-10):

* N4GOOGLE-01: limited folder ``hidden`` stores only its owner and inherits nothing;
  its subtree inherits from it. A reader of the parent ``Asana`` reads ``Asana`` only.
* N4GOOGLE-02: an item of another tenant's shared drive sits under a grant-less stub
  group and carries the member's grant itself.
* N4GOOGLE-03: a share with the Workspace's own domain is a grant to the
  ``domain:<name>`` group, whose members are that domain's users.
* N4GOOGLE-04: a file the syncing user can only view carries the owner's grant too.
"""

from __future__ import annotations

import pytest

from .fixture_graph import TS, _node, app, bt, ip, nr, perm, rec, rg, user_app
from .loaders import NODE_TARGETS, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-gd"
ORG_NODE = "gd-orgnode"
APP = "gd-app"
OWNER, TEST, OUTSIDER, MEMBER, EXT_OWNER = "gd-owner", "gd-test", "gd-outsider", "gd-member", "gd-extowner"
MY_DRIVE = "gd-mydrive"
ASANA, HIDDEN, HIDDEN_S1, PDF, BOX = "gd-asana", "gd-hidden", "gd-hidden-s1", "gd-pdf", "gd-box"
STUB, EXT_ROOT_FILE, EXT_FOLDER, EXT_CHILD = "gd-stub", "gd-ext-file", "gd-ext-folder", "gd-ext-child"
DOMAIN_GROUP, GANTT = "gd-domain-group", "gd-gantt"
SLACK_ALERTS = "gd-slack-alerts"
DRIVE = "DRIVE WORKSPACE"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _graph() -> tuple[list[dict], list[dict]]:
    item = {"connector_id": APP, "connectorName": DRIVE}
    users = (OWNER, TEST, OUTSIDER, MEMBER, EXT_OWNER)
    nodes = [
        _node("Organization", ORG_NODE, with_org=False, name="GD", accountType="enterprise", isActive=True),
        *(_org(_node("User", u, userId=f"m-{u}", email=f"{u}@example.com", fullName=u, isActive=True))
          for u in users),
        _org(app(APP, "Drive Workspace", connector=DRIVE, app_group="Google Workspace")),
        _org(rg(MY_DRIVE, "Owner's My Drive", group_type="DRIVE", connector=DRIVE, connectorId=APP)),
        _org(rec(ASANA, "Asana", recordGroupId=MY_DRIVE, **item)),
        _org(rec(HIDDEN, "hidden", recordGroupId=MY_DRIVE, **item)),
        _org(rec(HIDDEN_S1, "hidden s1", recordGroupId=MY_DRIVE, **item)),
        _org(rec(PDF, "100mb.pdf", recordGroupId=MY_DRIVE, **item)),
        _org(rec(BOX, "Box User group sync.txt", recordGroupId=MY_DRIVE, **item)),
        _org(rg(STUB, "0APG2DS8x81fJUk9PVA", group_type="DRIVE", connector=DRIVE, connectorId=APP)),
        _org(rec(EXT_ROOT_FILE, "100mb.pdf (other tenant)", recordGroupId=STUB, **item)),
        _org(rec(EXT_FOLDER, "Untitled folder", recordGroupId=STUB, **item)),
        _org(rec(EXT_CHILD, "chessHistory.doc", recordGroupId=STUB, **item)),
        _org(_node("Group", DOMAIN_GROUP, name="Everyone at example.com")),
        _org(rec(GANTT, "Gantt chart", recordGroupId=MY_DRIVE, **item)),
        _org(rec(SLACK_ALERTS, "Slack Alerts", **item)),
    ]
    edges = [
        *(bt(u, ORG_NODE, entity_type="ORGANIZATION") for u in users),
        *(user_app(u, APP) for u in users),
        nr(APP, MY_DRIVE), bt(MY_DRIVE, APP), perm(OWNER, MY_DRIVE, role="OWNER"),
        nr(MY_DRIVE, ASANA), bt(ASANA, MY_DRIVE), ip(ASANA, MY_DRIVE),
        perm(OWNER, ASANA, role="OWNER"), perm(TEST, ASANA, role="WRITER"),
        nr(ASANA, HIDDEN), bt(HIDDEN, MY_DRIVE), perm(OWNER, HIDDEN, role="OWNER"),
        nr(HIDDEN, HIDDEN_S1), bt(HIDDEN_S1, MY_DRIVE), ip(HIDDEN_S1, HIDDEN), perm(OWNER, HIDDEN_S1, role="OWNER"),
        nr(HIDDEN, PDF), bt(PDF, MY_DRIVE), ip(PDF, HIDDEN), perm(OWNER, PDF, role="OWNER"),
        nr(HIDDEN_S1, BOX), bt(BOX, MY_DRIVE), ip(BOX, HIDDEN_S1), perm(OWNER, BOX, role="OWNER"),
        nr(APP, STUB), bt(STUB, APP),
        nr(STUB, EXT_ROOT_FILE), bt(EXT_ROOT_FILE, STUB), ip(EXT_ROOT_FILE, STUB), perm(MEMBER, EXT_ROOT_FILE),
        nr(STUB, EXT_FOLDER), bt(EXT_FOLDER, STUB), ip(EXT_FOLDER, STUB),
        perm(MEMBER, EXT_FOLDER), perm(TEST, EXT_FOLDER),
        nr(EXT_FOLDER, EXT_CHILD), bt(EXT_CHILD, STUB), ip(EXT_CHILD, EXT_FOLDER), perm(MEMBER, EXT_CHILD),
        perm(OWNER, DOMAIN_GROUP), perm(TEST, DOMAIN_GROUP),
        nr(MY_DRIVE, GANTT), bt(GANTT, MY_DRIVE), ip(GANTT, MY_DRIVE),
        perm(OWNER, GANTT, role="OWNER"), perm(DOMAIN_GROUP, GANTT, grant_type="GROUP"),
        nr(APP, SLACK_ALERTS), perm(MEMBER, SLACK_ALERTS), perm(EXT_OWNER, SLACK_ALERTS, role="OWNER"),
    ]
    return nodes, edges


NODES, EDGES = _graph()
ALL = [n["id"] for n in NODES if n["kind"] in ("Record", "RecordGroup")]


class _Neo4j:
    def __init__(self, provider, settings) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        await load_into_neo4j(self.settings, NODES, EDGES)

    async def drop(self) -> None:
        await self.provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'gd-' DETACH DELETE n")


class _Arango:
    EDGES = ("permission", "belongsTo", "nodeRelations", "inheritPermissions", "userAppRelation")

    def __init__(self, provider, settings) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        await load_into_arango(self.settings, NODES, EDGES)

    async def drop(self) -> None:
        for c in self.EDGES:
            await self.provider.http_client.execute_aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/gd-') OR CONTAINS(e._to, '/gd-') REMOVE e IN @@c", {"@c": c})
        for c in set(NODE_TARGETS.values()):
            await self.provider.http_client.execute_aql(
                "FOR d IN @@c FILTER STARTS_WITH(d._key, 'gd-') REMOVE d IN @@c", {"@c": c})


@pytest.fixture(params=["neo4j", "arango"])
async def store(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    backend = (_Arango(arango_provider, arango_settings) if request.param == "arango"
               else _Neo4j(neo4j_provider, neo4j_settings))
    await backend.load()
    yield backend
    await backend.drop()


async def _admitted(provider, user: str, ids: list[str]) -> set[str]:
    return set((await provider.check_access(user, ORG, node_ids=ids)).node_ids)


LIMITED = [ASANA, HIDDEN, HIDDEN_S1, PDF, BOX]
OTHER_TENANT = [EXT_ROOT_FILE, EXT_FOLDER, EXT_CHILD]


async def test_a_parent_reader_does_not_reach_a_limited_folder_or_its_subtree(store) -> None:
    assert await _admitted(store.provider, TEST, LIMITED) == {ASANA}
    assert await _admitted(store.provider, OWNER, LIMITED) == set(LIMITED)


async def test_an_other_tenant_drive_member_reads_its_items_through_their_own_grants(store) -> None:
    assert await _admitted(store.provider, MEMBER, OTHER_TENANT) == set(OTHER_TENANT)
    assert await _admitted(store.provider, TEST, OTHER_TENANT) == {EXT_FOLDER, EXT_CHILD}
    assert await _admitted(store.provider, OUTSIDER, OTHER_TENANT + [STUB]) == set()


async def test_a_domain_share_reaches_the_domain_group_members_only(store) -> None:
    assert await _admitted(store.provider, TEST, [GANTT]) == {GANTT}
    assert await _admitted(store.provider, OUTSIDER, [GANTT]) == set()


async def test_the_owner_of_a_view_only_file_reads_it(store) -> None:
    assert await _admitted(store.provider, EXT_OWNER, [SLACK_ALERTS]) == {SLACK_ALERTS}
    assert await _admitted(store.provider, MEMBER, [SLACK_ALERTS]) == {SLACK_ALERTS}


async def test_nobody_else_gains_anything(store) -> None:
    assert await _admitted(store.provider, OUTSIDER, ALL) == set()

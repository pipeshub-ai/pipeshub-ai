"""The mailbox record grants removal on a graph in the shape main's Outlook wrote:
every mail and attachment granted to the mailbox owner and to each from/to/cc
address, and inheriting from its folder, which holds the owner's grant. After the
upgrade's migrations a recipient no longer reads another mailbox's copy and the
owner still reads it through the folder. Run on both backends."""

from __future__ import annotations

import pytest

from .fixture_graph import _node, app, bt, ip, nr, perm, rec, rg
from .loaders import NODE_TARGETS, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-mg"
OWNER, RECIPIENT = "mg-owner", "mg-rcpt"
OUTLOOK, DRIVE = "mg-outlook", "mg-drive"
CONNECTORS = ["OUTLOOK", "OUTLOOK PERSONAL", "GMAIL", "GMAIL WORKSPACE"]
TYPES = ["MAIL", "GROUP_MAIL", "FILE"]


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _graph() -> tuple[list[dict], list[dict]]:
    mail = {"connector_id": OUTLOOK, "connectorName": "OUTLOOK", "recordGroupId": "mg-inbox"}
    nodes = [
        _org(_node("User", OWNER, userId="mg-om", email="owner@example.com", fullName="Owner")),
        _org(_node("User", RECIPIENT, userId="mg-rm", email="rcpt@example.com", fullName="Recipient")),
        _org(app(OUTLOOK, "Outlook", connector="OUTLOOK", app_group="Microsoft 365")),
        _org(app(DRIVE, "Drive", connector="DRIVE", app_group="Google Workspace")),
        _org(rg("mg-inbox", "Inbox", group_type="MAILBOX", connector="OUTLOOK", connectorId=OUTLOOK,
                permissionModel="RECORD_GROUP_LEVEL")),
        _org(rec("mg-m1", "Owner's copy", record_type="MAIL", **mail)),
        _org(rec("mg-a1", "Its attachment", externalParentId="ext-mg-m1", **mail)),
        # Inherits nothing: its grant is its only audience.
        _org(rec("mg-k1", "No inheritance", record_type="MAIL", **mail)),
        # A folder removed since, and one that never got its owner's grant: the
        # owner would lose these mails with their grants.
        _org(rg("mg-gone", "Deleted folder", group_type="MAILBOX", connector="OUTLOOK", connectorId=OUTLOOK,
                permissionModel="RECORD_GROUP_LEVEL", isDeleted=True)),
        _org(rec("mg-x1", "In a deleted folder", record_type="MAIL", connector_id=OUTLOOK, connectorName="OUTLOOK",
                 recordGroupId="mg-gone")),
        _org(rg("mg-bare", "Folder without grants", group_type="MAILBOX", connector="OUTLOOK", connectorId=OUTLOOK,
                permissionModel="RECORD_GROUP_LEVEL")),
        _org(rec("mg-y1", "In a folder without grants", record_type="MAIL", connector_id=OUTLOOK,
                 connectorName="OUTLOOK", recordGroupId="mg-bare")),
        _org(rg("mg-shared", "Shared drive", group_type="DRIVE", connector="DRIVE", connectorId=DRIVE)),
        _org(rec("mg-d1", "Drive file", connector_id=DRIVE, connectorName="DRIVE", recordGroupId="mg-shared")),
    ]
    edges = [
        perm(OWNER, OUTLOOK), perm(RECIPIENT, OUTLOOK), perm(RECIPIENT, DRIVE),
        bt("mg-inbox", OUTLOOK), perm(OWNER, "mg-inbox", role="OWNER"),
        bt("mg-m1", "mg-inbox"), ip("mg-m1", "mg-inbox"),
        perm(OWNER, "mg-m1", role="OWNER"), perm(RECIPIENT, "mg-m1"),
        bt("mg-a1", "mg-inbox"), ip("mg-a1", "mg-inbox"), nr("mg-m1", "mg-a1", "ATTACHMENT"),
        perm(OWNER, "mg-a1", role="OWNER"), perm(RECIPIENT, "mg-a1"),
        bt("mg-k1", "mg-inbox"), perm(RECIPIENT, "mg-k1"),
        bt("mg-gone", OUTLOOK), perm(OWNER, "mg-gone", role="OWNER"),
        bt("mg-x1", "mg-gone"), ip("mg-x1", "mg-gone"), perm(OWNER, "mg-x1", role="OWNER"), perm(RECIPIENT, "mg-x1"),
        bt("mg-bare", OUTLOOK),
        bt("mg-y1", "mg-bare"), ip("mg-y1", "mg-bare"), perm(OWNER, "mg-y1", role="OWNER"), perm(RECIPIENT, "mg-y1"),
        bt("mg-shared", DRIVE),
        bt("mg-d1", "mg-shared"), ip("mg-d1", "mg-shared"), perm(RECIPIENT, "mg-d1"),
    ]
    return nodes, edges


NODES, EDGES = _graph()
KIND = {n["id"]: n["kind"] for n in NODES}


class _Neo4j:
    def __init__(self, provider, settings) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        await load_into_neo4j(self.settings, NODES, EDGES)

    async def drop(self) -> None:
        await self.provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'mg-' DETACH DELETE n")

    async def grants(self, record: str) -> int:
        rows = await self.provider.client.execute_query(
            "MATCH ()-[p:PERMISSION]->(:Record {id: $r}) RETURN count(p) AS n", parameters={"r": record})
        return rows[0]["n"]


class _Arango:
    EDGES = ("permission", "belongsTo", "nodeRelations", "inheritPermissions")

    def __init__(self, provider, settings) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        # A record group cannot carry isDeleted on ArangoDB (strict schema): a gone folder is a missing document.
        await load_into_arango(self.settings, [n for n in NODES if n["id"] != "mg-gone"], EDGES)

    async def drop(self) -> None:
        for c in self.EDGES:
            await self.provider.http_client.execute_aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/mg-') OR CONTAINS(e._to, '/mg-') REMOVE e IN @@c", {"@c": c})
        for c in set(NODE_TARGETS.values()):
            await self.provider.http_client.execute_aql(
                "FOR d IN @@c FILTER STARTS_WITH(d._key, 'mg-') REMOVE d IN @@c", {"@c": c})

    async def grants(self, record: str) -> int:
        rows = await self.provider.http_client.execute_aql(
            "RETURN LENGTH(FOR p IN permission FILTER p._to == @r RETURN 1)", {"r": f"records/{record}"})
        return rows[0]


@pytest.fixture(params=["neo4j", "arango"])
async def store(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    backend = (_Arango(arango_provider, arango_settings) if request.param == "arango"
               else _Neo4j(neo4j_provider, neo4j_settings))
    await backend.load()
    yield backend
    await backend.drop()


async def _admitted(provider, user: str, ids: list[str]) -> set[str]:
    return set((await provider.check_access(user, ORG, node_ids=ids)).node_ids)


async def test_a_recipient_loses_the_other_mailbox_and_the_owner_keeps_it(store) -> None:
    mails = ["mg-m1", "mg-a1"]
    assert await _admitted(store.provider, RECIPIENT, mails) == set(mails), "the premise: main's recipient grants"

    result = await store.provider.remove_inherited_record_grants(CONNECTORS, TYPES, batch_size=1)
    await store.provider.backfill_hierarchy()

    assert result == {"removed": 4, "records": 2}
    assert await _admitted(store.provider, RECIPIENT, mails) == set()
    assert await _admitted(store.provider, OWNER, mails) == set(mails)
    # A mail that inherits nothing keeps its grant, and other connectors are not touched.
    assert await store.grants("mg-k1") == 1
    # So does one whose folder is gone or does not carry its owner's grant: the owner keeps it.
    assert await store.grants("mg-x1") == 2
    assert await store.grants("mg-y1") == 2
    assert await _admitted(store.provider, OWNER, ["mg-y1"]) == {"mg-y1"}
    assert await store.grants("mg-d1") == 1
    assert await _admitted(store.provider, RECIPIENT, ["mg-d1"]) == {"mg-d1"}

    again = await store.provider.remove_inherited_record_grants(CONNECTORS, TYPES)
    assert again == {"removed": 0, "records": 0}


# What play and the QA stacks ran before the mailbox step existed.
SHAPES_BEFORE_NESTED_GROUP_ROOTS = [
    "collection_roots", "groups", "group_roots", "nested_inheritance", "cross_group_children",
]


async def test_on_a_stack_backfilled_before_the_removal_the_owner_keeps_the_attachment(store) -> None:
    """The earlier backfill left the attachment of a granted mail without
    inheritance from it. Once its grants go, its RECORD_GROUP_LEVEL folder (main
    wrote Outlook's folders so) still admits the owner, and the nested group roots
    step that follows hangs it off that folder."""
    mails = ["mg-m1", "mg-a1"]
    await store.provider.backfill_hierarchy(shapes=SHAPES_BEFORE_NESTED_GROUP_ROOTS)

    await store.provider.remove_inherited_record_grants(CONNECTORS, TYPES)

    assert await _admitted(store.provider, OWNER, mails) == set(mails)
    assert await _admitted(store.provider, RECIPIENT, mails) == set()

    added = await store.provider.backfill_hierarchy(shapes=["nested_group_roots"])

    assert added["added"]["nested_group_roots"] >= 1
    assert await _admitted(store.provider, OWNER, mails) == set(mails)
    assert await _admitted(store.provider, RECIPIENT, mails) == set()

"""The knowledge hub's parent of a node on PostgreSQL, for the same tree as ``../test_kh_parent_node_real_backends.py`` on
Neo4j and ArangoDB: a drive with a folder, a file in the folder, a file under that file and a file directly in the drive.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging

import pytest

from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org
from .test_kg_derivation import ctx, edge

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

FOLDER = "text/directory"


async def _tree(p: PostgresGraphProvider, org: str) -> None:
    def record(key: str, name: str, **kw: object) -> dict:
        return {"_key": key, "recordName": name, "connectorId": "app1", "connectorName": "DRIVE WORKSPACE", "orgId": org,
                "recordType": "FILE", "origin": "CONNECTOR", **kw}

    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE WORKSPACE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "drive", "groupName": "drive", "groupType": "DRIVE", "connectorId": "app1",
                                     "connectorName": "DRIVE WORKSPACE", "orgId": org}], "recordGroups")
        await p.batch_upsert_nodes([record("docs", "docs", mimeType=FOLDER), record("guide", "guide", mimeType="application/pdf"),
                                    record("figure", "figure", mimeType="application/pdf"), record("loose", "loose", mimeType="application/pdf")], "records")
        await p.batch_create_edges([edge("docs", "records", "guide", "records", relationshipType="PARENT_CHILD"),
                                    edge("guide", "records", "figure", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge(r, "records", "drive", "recordGroups") for r in ("docs", "guide", "figure", "loose")], "belongsTo")
        # As the processor writes a nested record: it inherits from the record above it, a top one from the group.
        await p.batch_create_edges([edge("guide", "records", "docs", "records"), edge("figure", "records", "guide", "records"),
                                    edge("docs", "records", "drive", "recordGroups"), edge("loose", "records", "drive", "recordGroups")],
                                   "inheritPermissions")


@pytest.fixture
async def tree(pg_db) -> tuple[PostgresGraphProvider, str]:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    await _tree(p, org)
    return p, org


async def _parent(tree: tuple[PostgresGraphProvider, str], node_id: str) -> dict | None:
    p, org = tree
    with ctx(org):
        return await p.get_knowledge_hub_parent_node(node_id, [FOLDER])


async def test_a_record_under_a_folder_has_the_folder_as_its_parent(tree) -> None:
    assert await _parent(tree, "guide") == {"id": "docs", "name": "docs", "nodeType": "folder", "subType": "FILE"}


async def test_a_record_under_a_record_that_is_not_a_folder_has_it_as_a_record_parent(tree) -> None:
    assert await _parent(tree, "figure") == {"id": "guide", "name": "guide", "nodeType": "record", "subType": "FILE"}


async def test_a_record_directly_in_a_group_has_the_group_as_its_parent(tree) -> None:
    assert await _parent(tree, "loose") == {"id": "drive", "name": "drive", "nodeType": "recordGroup", "subType": "DRIVE"}


async def test_a_group_has_its_app_as_parent_and_the_app_has_none(tree) -> None:
    assert await _parent(tree, "drive") == {"id": "app1", "name": "Drive", "nodeType": "app", "subType": "DRIVE WORKSPACE"}
    assert await _parent(tree, "app1") is None

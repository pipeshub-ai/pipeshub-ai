"""H2 RB-8, SYNC4 S7: main's ``check_access`` decides exactly what ``check_record_access_with_details`` decides, on a
real Neo4j and a real ArangoDB, in one query; since H5 through the shared record-access contract
(``tests/support/record_access_contract.py``), which the tests' in-memory fake is held to as well.

Requires the servers of ``tests/integration/compose/graph-db.yml`` (or any Neo4j 5 and ArangoDB 3.12 at
``NEO4J_TEST_URI`` / ``ARANGO_TEST_URL``); skipped when they are absent.
Run: pytest tests/integration/graph_db/test_record_access_batch.py -m integration

Seeded through ``IGraphDBProvider``'s own methods: one record per access path the single check reads, records the
user may not read for each reason, and another user's.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from tests.integration.graph_db.test_knowledge_publications import (
    _user,  # pyright: ignore[reportPrivateUsage]
    arango_provider,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    neo4j_provider,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    provider,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from tests.support.record_access_contract import (
    AccessWorld,
    assert_record_access_contract,
)

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

C = CollectionNames


def _hex() -> str:
    return uuid.uuid4().hex[:12]


async def _node(graph: IGraphDBProvider, collection: str, doc: dict[str, object]) -> None:
    assert await graph.batch_upsert_nodes([doc], collection) is not False


async def _edge(graph: IGraphDBProvider, collection: str, frm: tuple[str, str], to: tuple[str, str], **extra: object) -> None:
    edge = {"from_id": frm[1], "from_collection": frm[0], "to_id": to[1], "to_collection": to[0], "createdAtTimestamp": 1, **extra}
    assert await graph.batch_create_edges([edge], collection)


async def _record(
    graph: IGraphDBProvider, org: str, *, connector: str, origin: str = "CONNECTOR", name: str = "DRIVE",
    deleted: bool = False,
) -> str:
    rid = f"r-{_hex()}"
    await _node(graph, C.RECORDS.value, {
        "id": rid, "orgId": org, "recordName": rid, "externalRecordId": rid, "recordType": "FILE", "origin": origin,
        "connectorName": name, "connectorId": connector, "virtualRecordId": f"v-{rid}", "isDeleted": deleted,
        "indexingStatus": "COMPLETED", "version": 1, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    })
    await _node(graph, C.FILES.value, {"id": rid, "name": rid, "isFile": True})
    await _edge(graph, C.IS_OF_TYPE.value, (C.RECORDS.value, rid), (C.FILES.value, rid))
    return rid


async def test_rb8_the_batch_decides_what_the_single_check_decides(provider: IGraphDBProvider) -> None:  # noqa: F811
    graph = provider
    org, conn, other_conn, kb = (f"{p}-{_hex()}" for p in ("org", "conn", "conn", "kb"))
    await _node(graph, C.ORGS.value, {"id": org, "accountType": "enterprise", "isActive": True})
    for app, kind in ((conn, "DRIVE"), (other_conn, "DRIVE"), (kb, "KB")):
        # The connector gate reads only the org's own apps (``app.orgId``): an app without one gates nobody in.
        await _node(graph, C.APPS.value, {"id": app, "orgId": org, "name": app, "type": kind, "appGroup": "g", "scope": "team", "isActive": True, "createdAtTimestamp": 1})
        await _edge(graph, C.ORG_APP_RELATION.value, (C.ORGS.value, org), (C.APPS.value, app))
    user_key, user = await _user(graph, org)
    other_key, _ = await _user(graph, org)
    await _edge(graph, C.BELONGS_TO.value, (C.USERS.value, user_key), (C.ORGS.value, org), entityType="ORGANIZATION")
    await _edge(graph, C.USER_APP_RELATION.value, (C.USERS.value, user_key), (C.APPS.value, conn), syncState="COMPLETED", lastSyncUpdate=1)
    await _edge(graph, C.USER_APP_RELATION.value, (C.USERS.value, user_key), (C.APPS.value, kb), syncState="COMPLETED", lastSyncUpdate=1)

    def grant(frm: tuple[str, str], to: tuple[str, str], kind: str = "USER", role: str = "READER"):  # noqa: ANN202
        return _edge(graph, C.PERMISSION.value, frm, to, type=kind, role=role)

    me = (C.USERS.value, user_key)
    direct = await _record(graph, org, connector=conn)
    await grant(me, (C.RECORDS.value, direct))
    no_app = await _record(graph, org, connector=other_conn)
    await grant(me, (C.RECORDS.value, no_app))
    group, via_group = f"g-{_hex()}", await _record(graph, org, connector=conn)
    await _node(graph, C.GROUPS.value, {"id": group, "name": group, "orgId": org, "createdAtTimestamp": 1})
    await grant(me, (C.GROUPS.value, group))
    await grant((C.GROUPS.value, group), (C.RECORDS.value, via_group), kind="GROUP")
    rg, via_rg = f"rg-{_hex()}", await _record(graph, org, connector=conn)
    await _node(graph, C.RECORD_GROUPS.value, {
        "id": rg, "orgId": org, "groupName": rg, "groupType": "DRIVE", "connectorName": "DRIVE", "connectorId": conn, "createdAtTimestamp": 1,
    })
    await grant(me, (C.RECORD_GROUPS.value, rg))
    # The three edges a connector writes for a record in a group (``data_source_entities_processor``): inheritance
    # alone puts the record in no hierarchy, and the access check inherits only along PARENT_CHILD edges.
    await _edge(graph, C.BELONGS_TO.value, (C.RECORDS.value, via_rg), (C.RECORD_GROUPS.value, rg))
    await _edge(graph, C.INHERIT_PERMISSIONS.value, (C.RECORDS.value, via_rg), (C.RECORD_GROUPS.value, rg))
    await _edge(
        graph, C.NODE_RELATIONS.value, (C.RECORD_GROUPS.value, rg), (C.RECORDS.value, via_rg),
        relationshipType="PARENT_CHILD",
    )
    via_org = await _record(graph, org, connector=conn)
    await grant((C.ORGS.value, org), (C.RECORDS.value, via_org), kind="ORG")
    in_kb = await _record(graph, org, connector=kb, origin="UPLOAD", name="KB")
    await _edge(graph, C.BELONGS_TO.value, (C.RECORDS.value, in_kb), (C.APPS.value, kb), entityType="KB")
    await grant(me, (C.APPS.value, kb), role="OWNER")
    via_link = await _record(graph, org, connector=conn)
    await _node(graph, C.ANYONE.value, {"id": f"a-{_hex()}", "organization": org, "file_key": via_link, "role": "READER"})
    nobody = await _record(graph, org, connector=conn)
    others = await _record(graph, org, connector=conn)
    await grant((C.USERS.value, other_key), (C.RECORDS.value, others))
    trashed = await _record(graph, org, connector=conn, deleted=True)
    await grant(me, (C.RECORDS.value, trashed))
    missing = f"r-{_hex()}"

    await assert_record_access_contract(AccessWorld(
        access=graph,
        org_id=org,
        user_id=user,
        readable={"direct": direct, "group": via_group, "record_group": via_rg, "knowledge_base": in_kb},
        unreadable={
            "app_not_the_users": no_app, "no_grant": nobody, "another_users": others, "missing": missing,
            "in_the_trash": trashed, "anyone_link": via_link,
        },
        either={"org": via_org},
        user_key=user_key,
    ))


"""A record's pending virtual record on real Neo4j and ArangoDB: the holder lookups, the conditional commit and the indexes.

A record that replaces its indexed copy names the new virtual record as ``pendingVirtualRecordId`` while it keeps serving the old one.
The three holder lookups must report the pending vrid under its own id (not under the record's committed one), the commit must be one
conditional write that removes the field (never leaves null), and the lookups must stay index-only.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import Record, RecordType
from app.services.graph_db.conditions import EQ, ONE_OF, Cond
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

RECORDS = CollectionNames.RECORDS.value
ORG = "org-pending-it"
logger = logging.getLogger("pending-vrid-it")
RUNNING = ["IN_PROGRESS", "QUEUED", "NOT_STARTED"]


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "pending_vrid_it")
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:8]
        created: list[str] = []
        yield provider, run
        if created:
            await provider.delete_nodes(created, RECORDS)
        await provider.delete_nodes([f"{run}-a", f"{run}-b"], RECORDS)


async def _put(provider: IGraphDBProvider, key: str, vrid: str, status: str, **more: object) -> None:
    record = Record(
        id=key, org_id=ORG, virtual_record_id=vrid, record_name="r", record_type=RecordType.FILE, external_record_id=key,
        version=1, origin="UPLOAD", connector_id="conn", connector_name=Connectors.KNOWLEDGE_BASE,
    )
    assert await provider.batch_upsert_nodes(
        [{**record.to_arango_base_record(), "virtualRecordId": vrid, "indexingStatus": status, "md5Checksum": "m", **more}], RECORDS
    )


async def test_a_running_record_holds_its_pending_vrid_under_that_id(world) -> None:
    provider, run = world
    x, y = f"{run}-x", f"{run}-y"
    await _put(provider, f"{run}-a", x, "IN_PROGRESS", pendingVirtualRecordId=y)
    await _put(provider, f"{run}-b", x, "COMPLETED")
    assert await provider.get_records_by_virtual_record_id(y) == [f"{run}-a"]
    assert await provider.get_virtual_record_ids_in_use([y, f"{run}-none"]) == {y}
    holders = await provider.get_virtual_record_holders([x, y], ORG)
    assert [h["id"] for h in holders[y]] == [f"{run}-a"]  # reported under the pending id, not under the committed one
    assert sorted(h["id"] for h in holders[x]) == [f"{run}-a", f"{run}-b"]


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "EMPTY"])
async def test_any_other_status_releases_the_pending_vrid(world, status) -> None:
    provider, run = world
    y = f"{run}-y"
    await _put(provider, f"{run}-a", f"{run}-x", status, pendingVirtualRecordId=y)
    assert await provider.get_records_by_virtual_record_id(y) == []
    assert await provider.get_virtual_record_ids_in_use([y]) == set()
    assert y not in await provider.get_virtual_record_holders([y], ORG)


async def test_the_commit_is_one_conditional_write_that_removes_the_field(world) -> None:
    provider, run = world
    x, y, key = f"{run}-x", f"{run}-y", f"{run}-a"
    await _put(provider, key, x, "NOT_STARTED", pendingVirtualRecordId=y)  # edited faster than it indexes
    conditions = [Cond("virtualRecordId", EQ, x), Cond("pendingVirtualRecordId", EQ, y), Cond("indexingStatus", ONE_OF, RUNNING)]
    # a record that changed state refuses it
    assert not await provider.update_node_fields_if(
        key, RECORDS, {"virtualRecordId": y}, [*conditions[:2], Cond("indexingStatus", ONE_OF, ["COMPLETED"])], unset=["pendingVirtualRecordId"]
    )
    assert (await provider.get_document(key, RECORDS))["virtualRecordId"] == x
    assert await provider.update_node_fields_if(
        key, RECORDS, {"virtualRecordId": y, "indexingStatus": "COMPLETED", "isDirty": False}, conditions, unset=["pendingVirtualRecordId"]
    )
    doc = await provider.get_document(key, RECORDS)
    assert doc["virtualRecordId"] == y and doc["indexingStatus"] == "COMPLETED"
    assert "pendingVirtualRecordId" not in doc  # absent, not null: an older strict schema rejects a stored null
    # the same commit again matches nothing (it applies once)
    assert not await provider.update_node_fields_if(key, RECORDS, {"virtualRecordId": "z"}, conditions, unset=["pendingVirtualRecordId"])


async def test_the_pending_write_is_a_compare_and_set_on_what_it_read(world) -> None:
    provider, run = world
    key = f"{run}-a"
    await _put(provider, key, f"{run}-x", "COMPLETED")
    # absent expected as None, as the stores read an absent and a null field alike
    assert await provider.update_node_fields_if_match(
        key, RECORDS, {"pendingVirtualRecordId": "p1"}, {"virtualRecordId": f"{run}-x", "pendingVirtualRecordId": None}
    )
    assert not await provider.update_node_fields_if_match(
        key, RECORDS, {"pendingVirtualRecordId": "p2"}, {"virtualRecordId": f"{run}-x", "pendingVirtualRecordId": None}
    )
    assert await provider.update_node_fields_if_match(
        key, RECORDS, {}, {"pendingVirtualRecordId": "p1"}, unset=["pendingVirtualRecordId"]
    )
    assert "pendingVirtualRecordId" not in await provider.get_document(key, RECORDS)


async def test_the_pending_field_has_an_index_and_the_holder_lookup_does_not_scan(world) -> None:
    provider, run = world
    if isinstance(provider, Neo4jProvider):
        rows = await provider.client.execute_query("SHOW INDEXES YIELD name RETURN name")
        assert "record_pending_virtual_record_id" in {r["name"] for r in rows}
        return
    client = provider.http_client
    indexes = await client.get_indexes(RECORDS) if hasattr(client, "get_indexes") else None
    session = await client._get_session()
    async with session.get(f"{client.base_url}/_db/{client.database}/_api/index?collection={RECORDS}") as resp:
        listed = (await resp.json())["indexes"]
    assert any(i["fields"] == ["pendingVirtualRecordId"] and i.get("sparse") for i in listed), indexes or listed
    query = """FOR vrid IN @ids LET held = FIRST(FOR r IN records FILTER (r.virtualRecordId == vrid OR (r.pendingVirtualRecordId != null
        AND r.pendingVirtualRecordId == vrid AND r.indexingStatus IN ["IN_PROGRESS"])) LIMIT 1 RETURN 1) FILTER held != null RETURN vrid"""
    async with session.post(f"{client.base_url}/_db/{client.database}/_api/explain", json={"query": query, "bindVars": {"ids": ["a", "b"]}}) as resp:
        plan = await resp.json()
    kinds = {n["type"] for n in plan["plan"]["nodes"]}
    assert "IndexNode" in kinds or "SubqueryStartNode" in kinds, kinds


_SEED = 4000


async def _seed(provider: IGraphDBProvider, run: str) -> None:
    """Enough records that a scan would lose to an index seek."""
    template = Record(
        id="t", org_id=ORG, virtual_record_id="t", record_name="r", record_type=RecordType.FILE, external_record_id="t",
        version=1, origin="UPLOAD", connector_id="conn", connector_name=Connectors.KNOWLEDGE_BASE,
    ).to_arango_base_record()
    for start in range(0, _SEED, 500):
        docs = [
            {**template, "id": f"{run}-bulk-{i}", "_key": f"{run}-bulk-{i}", "externalRecordId": f"{run}-ext-{i}",
             "virtualRecordId": f"{run}-v{i}", "indexingStatus": "COMPLETED"}
            for i in range(start, min(start + 500, _SEED))
        ]
        assert await provider.batch_upsert_nodes(docs, RECORDS)


async def _unseed(provider: IGraphDBProvider, run: str) -> None:
    if isinstance(provider, Neo4jProvider):
        await provider.client.execute_query("MATCH (n:Record) WHERE n.id STARTS WITH $p DETACH DELETE n", parameters={"p": f"{run}-bulk-"})
    else:
        await provider.http_client.execute_aql(f"FOR d IN {RECORDS} FILTER STARTS_WITH(d._key, @p) REMOVE d IN {RECORDS}", {"p": f"{run}-bulk-"})


async def _plan_operators(provider: IGraphDBProvider) -> tuple[set[str], str]:
    """The operators of the plan, and the text of the indexes it seeks (their fields), of the correlated batch lookup (the form the orphan sweep asks), as the store plans it."""
    from app.services.graph_db.common.record_visibility import (
        aql_holds_vrid,
        cypher_holds_vrid,
    )

    if isinstance(provider, Neo4jProvider):
        query = f"EXPLAIN UNWIND ['a', 'b'] AS vrid MATCH (r:Record) WHERE {cypher_holds_vrid('r', 'vrid')} RETURN DISTINCT vrid"
        async with provider.client.driver.session(database=provider.client.database) as session:
            summary = await (await session.run(query)).consume()
        found: set[str] = set()
        seeks: list[str] = []

        def walk(plan: dict) -> None:
            kind = (plan.get("operatorType") or plan["operator_type"]).split("@")[0]
            found.add(kind)
            if "IndexSeek" in kind:
                seeks.append(str(plan.get("args", {}).get("Details", "")))
            for child in plan.get("children", []):
                walk(child)

        walk(summary.plan)
        return found, " ".join(seeks)
    client = provider.http_client
    session = await client._get_session()
    query = f"FOR vrid IN ['a', 'b'] LET held = FIRST(FOR r IN records FILTER {aql_holds_vrid('r', 'vrid')} LIMIT 1 RETURN 1) FILTER held != null RETURN vrid"
    async with session.post(f"{client.base_url}/_db/{client.database}/_api/explain", json={"query": query}) as resp:
        plan = await resp.json()
    nodes = plan["plan"]["nodes"]
    return {n["type"] for n in nodes}, " ".join(str(i["fields"]) for n in nodes for i in n.get("indexes", []))


_SCANS = {"NodeByLabelScan", "AllNodesScan", "EnumerateCollectionNode"}


async def test_the_batch_holder_lookup_plans_to_index_seeks_and_the_plan_stops_using_the_pending_index_without_it(world) -> None:
    """The plan of the sweep's lookup, on a store with enough records, uses no scan; with the pending index dropped it scans, so the
    assertion would catch a missing index. The index is made again afterwards."""
    provider, run = world
    await _seed(provider, run)
    try:
        operators, seeks = await _plan_operators(provider)
        assert not operators & _SCANS, operators
        assert "pendingVirtualRecordId" in seeks, seeks  # the alias arm is served by its own index
        if isinstance(provider, Neo4jProvider):
            assert any("IndexSeek" in o for o in operators), operators
            await provider.client.execute_query("DROP INDEX record_pending_virtual_record_id IF EXISTS")
            try:
                _, control = await _plan_operators(provider)
                assert "pendingVirtualRecordId" not in control, control  # the control: without the index the plan cannot use it
            finally:
                await provider.client.execute_query(
                    "CREATE INDEX record_pending_virtual_record_id IF NOT EXISTS FOR (n:Record) ON (n.pendingVirtualRecordId)"
                )
                await provider.client.execute_query("CALL db.awaitIndexes(60)")
        else:
            client = provider.http_client
            session = await client._get_session()
            async with session.get(f"{client.base_url}/_db/{client.database}/_api/index?collection={RECORDS}") as resp:
                pending = next(i for i in (await resp.json())["indexes"] if i["fields"] == ["pendingVirtualRecordId"])
            async with session.delete(f"{client.base_url}/_db/{client.database}/_api/index/{pending['id']}") as resp:
                assert resp.status == 200
            try:
                _, control = await _plan_operators(provider)
                assert "pendingVirtualRecordId" not in control, control  # the control
            finally:
                assert await client.ensure_persistent_index(RECORDS, ["pendingVirtualRecordId"], sparse=True)
    finally:
        await _unseed(provider, run)


@pytest.mark.arango_only  # the older strict schema is ArangoDB's; the Neo4j leg does not collect it (a skip would fail that leg)
async def test_a_swapped_record_stays_writable_under_the_older_strict_schema(world) -> None:
    """the commit removes the pending attribute, so a process that re-applies the schema of the release before this one
    can still write to every record that swapped (a stored null would be rejected by its closed rule). A record mid-swap carries the attribute
    and is the one that waits for a process of the new release."""
    provider, run = world
    import copy

    from app.schema.arango.documents import record_schema

    x, y, key = f"{run}-x", f"{run}-y", f"{run}-a"
    await _put(provider, key, x, "IN_PROGRESS", pendingVirtualRecordId=y)
    assert await provider.update_node_fields_if(
        key, RECORDS, {"virtualRecordId": y, "indexingStatus": "COMPLETED"},
        [Cond("virtualRecordId", EQ, x), Cond("pendingVirtualRecordId", EQ, y), Cond("indexingStatus", ONE_OF, RUNNING)], unset=["pendingVirtualRecordId"],
    )
    older = copy.deepcopy(record_schema)
    del older["rule"]["properties"]["pendingVirtualRecordId"]
    client = provider.http_client
    session = await client._get_session()
    url = f"{client.base_url}/_db/{client.database}/_api/collection/{RECORDS}/properties"
    async with session.put(url, json={"schema": older}) as resp:
        assert resp.status == 200
    try:
        assert await provider.update_node(key, RECORDS, {"indexingStatus": "FAILED", "reason": "x"})  # the older rule accepts the committed record
        # the control: the same rule rejects a record that still carries the attribute
        await _put(provider, f"{run}-b", x, "IN_PROGRESS")
        with pytest.raises(Exception, match="schema|1620"):
            await provider.update_node(f"{run}-b", RECORDS, {"pendingVirtualRecordId": y})
    finally:
        async with session.put(url, json={"schema": record_schema}) as resp:
            assert resp.status == 200


@pytest.mark.arango_only  # a sub-document merges on ArangoDB's update; the other stores keep their own rules for it
async def test_a_write_that_removes_the_pending_field_still_merges_a_sub_document_and_removes_a_null(world) -> None:
    provider, run = world
    key = f"{run}-a"
    await _put(provider, key, f"{run}-x", "IN_PROGRESS", pendingVirtualRecordId=f"{run}-y", recordProps={"a": 1, "b": 2}, reason="r")
    assert await provider.update_node_fields_if_match(
        key, RECORDS, {"recordProps": {"a": 3}, "reason": None}, {"pendingVirtualRecordId": f"{run}-y"}, unset=["pendingVirtualRecordId"]
    )
    doc = await provider.get_document(key, RECORDS)
    assert doc["recordProps"] == {"a": 3, "b": 2}  # merged, as without unset
    assert "reason" not in doc and "pendingVirtualRecordId" not in doc

"""`get_content_owner_connector_ids` against a real Neo4j and a real ArangoDB.

Seeds a scope KB whose records share content (virtual record ids) with two other
KBs of the same org and with a KB of another org, plus a trashed record in the
scope. Only same-org connectors outside the scope may come back, counting the
content live records of the scope share and how many contents each holds. Both
backends must answer the same.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.interface.graph_db_provider import SharedContentCount
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "content_owner_it"
logger = logging.getLogger("content-owner-it")


async def _remove(w: SimpleNamespace) -> None:
    if isinstance(w.g, Neo4jProvider):
        await w.g.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": w.ids})
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value):
        await w.g.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": w.ids})
    edges = CollectionNames.IS_OF_TYPE.value
    await w.g.http_client.execute_aql(
        f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids REMOVE e IN {edges}", {"ids": w.ids})


async def _set_virtual_record_ids(w: SimpleNamespace, vids: dict[str, str]) -> None:
    # Indexing writes virtualRecordId; the record upsert does not.
    rows = [{"id": rid, "vid": vid} for rid, vid in vids.items()]
    if isinstance(w.g, Neo4jProvider):
        await w.g.client.execute_query(
            "UNWIND $rows AS row MATCH (r:Record {id: row.id}) SET r.virtualRecordId = row.vid",
            parameters={"rows": rows},
        )
        return
    records = CollectionNames.RECORDS.value
    await w.g.http_client.execute_aql(
        f"FOR row IN @rows UPDATE {{_key: row.id, virtualRecordId: row.vid}} IN {records}", {"rows": rows})


@pytest.fixture(params=["neo4j", "arango"])
async def w(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            g = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                       else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(g, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        org, other_org = f"org-co-{run}", f"org-co2-{run}"
        scope, owner, minor, foreign = (f"kb-{name}-{run}" for name in ("scope", "owner", "minor", "foreign"))
        v = {name: f"{name}-{run}" for name in ("a", "b", "c", "gone", "own")}
        rows = [
            (scope, org, v["a"], False), (scope, org, v["b"], False), (scope, org, v["c"], False),
            (scope, org, v["gone"], True),
            (owner, org, v["a"], False), (owner, org, v["b"], False), (owner, org, v["own"], False),
            (owner, org, v["gone"], False), (owner, org, v["own"], False),
            (minor, org, v["c"], True),
            (foreign, other_org, v["a"], False),
        ]
        records = [
            FileRecord(
                id=f"rec-{i}-{run}", org_id=o, record_name=f"r{i}", record_type=RecordType.FILE,
                external_record_id=f"ext-{i}-{run}", version=1, origin=OriginTypes.UPLOAD,
                connector_name=Connectors.KNOWLEDGE_BASE, connector_id=cid,
                mime_type="application/pdf", indexing_status=ProgressStatus.COMPLETED.value,
                is_file=True, extension="pdf", virtual_record_id=vid, is_deleted=deleted,
            )
            for i, (cid, o, vid, deleted) in enumerate(rows)
        ]
        world = SimpleNamespace(g=g, org=org, scope=scope, owner=owner, minor=minor, ids=[r.id for r in records])
        cleanup.push_async_callback(_remove, world)
        await g.batch_upsert_records(records)
        await _set_virtual_record_ids(world, {r.id: r.virtual_record_id for r in records})
        yield world


async def test_owners_of_shared_content(w: SimpleNamespace) -> None:
    assert await w.g.get_content_owner_connector_ids([w.scope], w.org) == {
        # a and b; "gone" is held only by a trashed record of the scope. Its five records
        # hold four contents: two of them are copies of one.
        w.owner: SharedContentCount(shared=2, held=4),
        # Its own copy is trashed, but the stored file stays where it was written.
        w.minor: SharedContentCount(shared=1, held=1),
    }


async def test_a_connector_in_the_scope_is_never_an_owner(w: SimpleNamespace) -> None:
    assert set(await w.g.get_content_owner_connector_ids([w.scope, w.owner], w.org)) == {w.minor}


async def test_another_org_sees_nothing(w: SimpleNamespace) -> None:
    assert await w.g.get_content_owner_connector_ids([w.scope], "org-nobody") == {}

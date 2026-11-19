"""``get_record_by_id(raise_on_error=True)`` on a real Neo4j and ArangoDB: what the record-content resolver asks for.

Asked to raise, the read still answers a record that is there (typed) and None for one that is not: only a failed read
raises, which the unit tests drive on each provider class (``tests/unit/services/graph_db/test_get_record_by_id_raise.py``).
PostgreSQL is covered in ``postgres/test_records.py``.

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import FileRecord, RecordType
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

logger = logging.getLogger(__name__)
ARANGO_DB = "record_by_id_raise_it"


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    try:
        provider = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
    except Exception as exc:
        backend_unavailable(request.param, exc)
    try:
        yield provider
    finally:
        with contextlib.suppress(Exception):
            await provider.disconnect()


async def _remove(graph: IGraphDBProvider, record_id: str) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query("MATCH (n) WHERE n.id = $id DETACH DELETE n", parameters={"id": record_id})
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value):
        await graph.http_client.execute_aql(f"FOR d IN {collection} FILTER d._key == @id REMOVE d IN {collection}", {"id": record_id})
    await graph.http_client.execute_aql(
        f"FOR e IN {CollectionNames.IS_OF_TYPE.value} FILTER PARSE_IDENTIFIER(e._from).key == @id "
        f"REMOVE e IN {CollectionNames.IS_OF_TYPE.value}", {"id": record_id},
    )


async def test_asked_to_raise_a_record_is_read_typed_and_a_missing_one_is_none(graph: IGraphDBProvider) -> None:
    record_id = f"rec-raise-{uuid.uuid4().hex[:12]}"
    record = FileRecord(
        id=record_id, org_id=f"org-raise-{record_id}", record_name="report.pdf", record_type=RecordType.FILE,
        external_record_id=f"ext-{record_id}", version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE, connector_id=f"drive-{record_id}", mime_type="application/pdf",
        weburl=f"https://example.com/{record_id}", indexing_status=ProgressStatus.COMPLETED.value, is_file=True,
        extension="pdf", size_in_bytes=1024,
    )
    await graph.batch_upsert_records([record])
    try:
        got = await graph.get_record_by_id(record_id, raise_on_error=True)
        assert isinstance(got, FileRecord) and got.id == record_id
        assert await graph.get_record_by_id(f"no-such-{record_id}", raise_on_error=True) is None
    finally:
        await _remove(graph, record_id)

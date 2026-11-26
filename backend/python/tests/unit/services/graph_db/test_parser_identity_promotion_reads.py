"""A promotion of queued duplicates by parser identity must not run on a read that failed.

A ``files`` read that fails answers None; with a record name that has no suffix the duplicate's parser then reads as
"not tracked" and it would be promoted without the check. The read has to raise, so the promotion fails and the duplicate
stays queued.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

REF = {"_key": "r1", "id": "r1", "orgId": "org-1", "md5Checksum": "abc", "extractionStatus": "COMPLETED"}
DUP = {"_key": "r2", "id": "r2", "md5Checksum": "abc", "indexingStatus": "QUEUED", "recordName": "notes", "mimeType": "application/octet-stream"}


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")


def _files_read_that_fails() -> AsyncMock:
    async def get(*_a: object, raise_on_error: bool = False, **_k: object) -> dict[str, str] | None:
        if raise_on_error:
            raise RuntimeError("files read failed")
        return {}  # a failed read answers an empty document when it is not asked to raise

    return AsyncMock(side_effect=get)


@pytest.mark.asyncio
async def test_arango_promotion_fails_closed_when_the_files_read_fails() -> None:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql.side_effect = [[dict(REF)], [dict(DUP)]]
    provider.get_document = _files_read_that_fails()  # type: ignore[method-assign]
    provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

    result = await provider.update_queued_duplicates_status(record_id="r1", new_indexing_status="COMPLETED", virtual_record_id="v-1")

    assert result == -1
    provider.batch_update_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_neo4j_promotion_fails_closed_when_the_files_read_fails() -> None:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=[[{"record": dict(REF)}], [{"record": dict(DUP)}]])
    provider._neo4j_to_arango_node = MagicMock(return_value=dict(DUP))  # type: ignore[method-assign]
    provider.get_document = _files_read_that_fails()  # type: ignore[method-assign]
    provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

    result = await provider.update_queued_duplicates_status("r1", "COMPLETED", virtual_record_id="v-1")

    assert result == -1
    provider.batch_update_nodes.assert_not_awaited()

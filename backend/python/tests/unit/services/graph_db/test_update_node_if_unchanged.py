"""``update_node_if_unchanged``: on ArangoDB a plain document update guarded by the snapshot's revision (no AQL)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


def _provider(patch_result: bool = True) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logging.getLogger("t"), MagicMock())
    provider.http_client = MagicMock()
    provider.http_client.patch_document_if_match = AsyncMock(return_value=patch_result)
    provider.http_client.execute_aql = AsyncMock(return_value=[1])
    return provider


async def test_a_snapshot_with_a_revision_is_one_conditional_document_update_and_no_query() -> None:
    provider = _provider()

    ok = await provider.update_node_if_unchanged("k", "records", {"a": 1}, {"_rev": "_r1", "contentRev": "x"}, {"contentRev": "x"})

    assert ok is True
    provider.http_client.patch_document_if_match.assert_awaited_once_with("records", "k", {"a": 1}, "_r1", txn_id=None)
    provider.http_client.execute_aql.assert_not_awaited()


async def test_a_record_that_changed_since_the_snapshot_is_refused_by_the_server() -> None:
    assert await _provider(patch_result=False).update_node_if_unchanged("k", "records", {"a": 1}, {"_rev": "_r1", "contentRev": "x"}, {"contentRev": "x"}) is False


async def test_a_snapshot_that_does_not_hold_what_is_expected_writes_nothing() -> None:
    provider = _provider()

    assert await provider.update_node_if_unchanged("k", "records", {"a": 1}, {"_rev": "_r1", "contentRev": "y"}, {"contentRev": "x"}) is False
    provider.http_client.patch_document_if_match.assert_not_awaited()


async def test_a_snapshot_without_a_revision_uses_the_field_checked_statement() -> None:
    provider = _provider()

    assert await provider.update_node_if_unchanged("k", "records", {"a": 1}, {"contentRev": "x"}, {"contentRev": "x"}) is True
    provider.http_client.patch_document_if_match.assert_not_awaited()
    provider.http_client.execute_aql.assert_awaited_once()

"""A record delete that hits an Arango write-write conflict is retried in its own
transaction, and a failed REMOVE rolls back instead of committing edge-less records."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

CONFLICT = 'Query failed (status=409): {"errorMessage":"AQL: write-write conflict","errorNum":1200}'


def _provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.http_client = MagicMock()
    return p


@pytest.mark.asyncio
async def test_a_conflict_in_its_own_transaction_is_retried() -> None:
    p = _provider()
    p._delete_records_recursive_once = AsyncMock(side_effect=[
        {"success": False, "reason": CONFLICT}, {"success": True, "successfully_deleted": 1},
    ])
    result = await p.delete_records_recursive(["r1"], "kb1")
    assert result["success"] is True
    assert p._delete_records_recursive_once.await_count == 2


@pytest.mark.asyncio
async def test_a_callers_transaction_is_not_retried() -> None:
    p = _provider()
    p._delete_single_record_once = AsyncMock(return_value={"success": False, "reason": CONFLICT})
    result = await p.delete_single_record("r1", transaction="txn-1")
    assert result["success"] is False
    assert p._delete_single_record_once.await_count == 1


@pytest.mark.asyncio
async def test_other_failures_are_not_retried() -> None:
    p = _provider()
    p._delete_records_recursive_once = AsyncMock(return_value={"success": False, "reason": "boom"})
    await p.delete_records_recursive(["r1"], "kb1")
    assert p._delete_records_recursive_once.await_count == 1


@pytest.mark.asyncio
async def test_a_remove_that_misses_a_record_raises() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(return_value=[1])
    with pytest.raises(RuntimeError, match="Removed 1 of 2"):
        await p._remove_records_or_raise("txn", ["a", "b"])


@pytest.mark.asyncio
async def test_a_remove_error_propagates_with_its_arango_message() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(side_effect=RuntimeError(CONFLICT))
    with pytest.raises(RuntimeError, match="write-write conflict"):
        await p._remove_records_or_raise("txn", ["a"])


@pytest.mark.asyncio
async def test_a_failure_in_a_callers_transaction_raises() -> None:
    """The caller rolls back and may retry, so it gets the error itself."""
    p = _provider()
    p._get_all_edge_collections = AsyncMock(return_value=["permission"])
    p.execute_query = AsyncMock(side_effect=RuntimeError("deadlock"))
    with pytest.raises(RuntimeError, match="deadlock"):
        await p.delete_single_record("r1", transaction="txn-1")

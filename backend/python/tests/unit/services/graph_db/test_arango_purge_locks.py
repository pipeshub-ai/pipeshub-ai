"""The ArangoDB purge reports a lock it could not take as such, and nothing else.

``GraphLockUnavailableError`` makes the purge pause without counting a failure
against any record, so a failure that is not about locks must not take that path.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.graph_db_exceptions import GraphLockUnavailableError
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

# As ArangoDB 3.12 answers a begin whose exclusive lock times out (checked against a real server).
LOCK_TIMEOUT = Exception(
    'Failed to begin transaction: {"code":500,"error":true,"errorMessage":"timed out after 1 s '
    'waiting for exclusive-lock on collection soft_delete_it/recordRelations on single","errorNum":18}'
)
WRITE_CONFLICT = Exception('Failed to begin transaction: {"code":409,"error":true,"errorNum":1200}')
class CollectionMissing(Exception):
    """A begin that failed for a reason that has nothing to do with locks."""


NOT_A_LOCK = CollectionMissing(
    'Failed to begin transaction: {"code":404,"error":true,"errorMessage":"collection or view not found",'
    '"errorNum":1203}'
)


def _provider(error: Exception) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logging.getLogger("purge-locks-test"), MagicMock())
    provider.http_client = MagicMock()
    provider.http_client.begin_transaction = AsyncMock(side_effect=error)
    provider._get_all_edge_collections = AsyncMock(return_value=["recordRelations", "belongsTo"])
    return provider


@pytest.mark.parametrize("error", [LOCK_TIMEOUT, WRITE_CONFLICT], ids=["lock-timeout", "write-conflict"])
async def test_a_lock_the_purge_could_not_take_is_reported_as_such(error: Exception) -> None:
    with pytest.raises(GraphLockUnavailableError):
        await _provider(error).purge_trashed_records(["r1"], "org-1", 10)
    with pytest.raises(GraphLockUnavailableError):
        await _provider(error).purge_trash_kept_record_groups("org-1")


async def test_any_other_failure_is_raised_as_it_is() -> None:
    for call in (
        lambda p: p.purge_trashed_records(["r1"], "org-1", 10),
        lambda p: p.purge_trash_kept_record_groups("org-1"),
    ):
        with pytest.raises(CollectionMissing) as raised:
            await call(_provider(NOT_A_LOCK))
        assert raised.value is NOT_A_LOCK

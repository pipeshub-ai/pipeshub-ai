"""A graph outage is not "record not found" on the ArangoDB record access check.

``check_record_access_with_details`` answers None for a user, record or grant that
does not exist (the route says "no access"). A failed lookup is a different
answer: the check raises, as the Neo4j provider's does, so the route reports the
failure instead of telling the user a file is gone.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


class _Down(Exception):
    pass


def _provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.http_client = MagicMock(
        execute_aql=AsyncMock(return_value=[]),
        get_document=AsyncMock(return_value=None),
    )
    return p


USER = {"_key": "k1", "id": "k1", "userId": "u1", "orgId": "o1"}


class TestRecordAccessCheckOnAnOutage:
    async def test_a_failed_user_lookup_raises(self) -> None:
        p = _provider()
        p.http_client.execute_aql = AsyncMock(side_effect=_Down("db down"))
        with pytest.raises(_Down):
            await p.check_record_access_with_details("u1", "o1", "r1")

    async def test_an_unknown_user_is_still_none(self) -> None:
        p = _provider()
        assert await p.check_record_access_with_details("u1", "o1", "r1") is None

    async def test_a_failed_app_lookup_raises(self) -> None:
        p = _provider()
        p.get_user_by_user_id = AsyncMock(return_value=USER)
        p.get_gated_apps = AsyncMock(side_effect=_Down("db down"))
        with pytest.raises(_Down):
            await p.check_record_access_with_details("u1", "o1", "r1")

    async def test_a_failed_record_read_raises(self) -> None:
        p = _provider()
        p.get_user_by_user_id = AsyncMock(return_value=USER)
        p._get_user_app_ids = AsyncMock(return_value=[])
        p.http_client.get_document = AsyncMock(side_effect=_Down("db down"))
        with pytest.raises(_Down):
            await p.check_record_access_with_details("u1", "o1", "r1")

    async def test_a_missing_record_is_still_none(self) -> None:
        p = _provider()
        p.get_user_by_user_id = AsyncMock(return_value=USER)
        p._get_user_app_ids = AsyncMock(return_value=[])
        assert await p.check_record_access_with_details("u1", "o1", "r1") is None

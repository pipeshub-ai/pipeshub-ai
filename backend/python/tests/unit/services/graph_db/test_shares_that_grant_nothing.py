"""Domain, "anyone" and link shares grant no access, in both graph providers.

PipesHub deliberately does not turn a source's domain-wide, "anyone" or
"anyone with the link" share into access. Nothing writes those grants today,
but the queries that decide access still read them, so any such data left from
an older version made a record reachable by everyone in the org. These pin
that every access query ignores them: opening a record, the per-record
permission checks, and the per-connector map search and chat use.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


class _Recorder:
    """Stands in for a driver: remembers every query and answers from a script."""

    def __init__(self, *answers: object) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._answers = list(answers)

    async def __call__(self, query: str, *args: object, **kwargs: object) -> object:
        params = kwargs.get("parameters") or kwargs.get("bind_vars") or (args[0] if args else {}) or {}
        self.calls.append((query, dict(params)))
        return self._answers.pop(0) if self._answers else []

    def text(self) -> str:
        return "\n".join(query for query, _ in self.calls)

    def params(self) -> list[dict]:
        return [params for _, params in self.calls]


def _neo4j(recorder: _Recorder) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock(), accessible_records_cache=None)
    provider.client = MagicMock()
    provider.client.execute_query = recorder
    provider.get_document = AsyncMock(return_value={"id": "rec-1", "origin": "UPLOAD"})
    provider._get_user_app_ids = AsyncMock(return_value=[])
    return provider


def _arango(recorder: _Recorder) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = recorder
    provider.execute_query = recorder
    provider.get_document = AsyncMock(return_value={"_key": "rec-1", "origin": "UPLOAD"})
    provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1", "userId": "user-1"})
    provider._get_user_app_ids = AsyncMock(return_value=[])
    return provider


def _assert_reads_no_share_grants(recorder: _Recorder) -> None:
    text = recorder.text()
    assert recorder.calls, "no query was run, so nothing was checked"
    assert "Anyone" not in text and "@@anyone" not in text, "a query still reads 'anyone' grants"
    assert '"DOMAIN"' not in text, "a query still grants access through a domain share"
    for params in recorder.params():
        assert "@anyone" not in params


# ---- opening a record --------------------------------------------------------


@pytest.mark.asyncio
async def test_neo4j_record_open_ignores_a_legacy_anyone_grant() -> None:
    # What an older access query returned for a record reachable only through an
    # Anyone node: an entry with no source. It must not count as access.
    legacy = [{"allAccess": [{"type": "ANYONE", "source": None, "role": "READER"}]}]
    recorder = _Recorder([{"u": {"id": "user-key-1"}}], legacy)
    provider = _neo4j(recorder)

    assert await provider.check_record_access_with_details("user-1", "org-1", "rec-1") is None
    _assert_reads_no_share_grants(recorder)


@pytest.mark.asyncio
async def test_arango_record_open_reads_no_anyone_grant() -> None:
    recorder = _Recorder([None])
    provider = _arango(recorder)

    assert await provider.check_record_access_with_details("user-1", "org-1", "rec-1") is None
    _assert_reads_no_share_grants(recorder)


# ---- per-record permission checks (reindex, update, delete gates) --------------


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_record_permission_check_reads_no_domain_or_anyone_grant(make) -> None:
    recorder = _Recorder([])
    provider = make(recorder)

    result = await provider._check_record_permissions("rec-1", "user-key-1")

    assert result.get("permission") is None
    _assert_reads_no_share_grants(recorder)
    # Organization-wide shares still count; only the three share kinds are dropped.
    assert "ORG" in recorder.text()


@pytest.mark.asyncio
async def test_arango_drive_permission_check_reads_no_domain_or_anyone_grant() -> None:
    recorder = _Recorder([None])
    provider = _arango(recorder)

    assert await provider._check_drive_permissions("rec-1", "user-key-1") is None
    _assert_reads_no_share_grants(recorder)


# ---- what search and chat may retrieve ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_connector_search_map_reads_no_anyone_grant(make) -> None:
    recorder = _Recorder([])
    provider = make(recorder)

    assert await provider._get_virtual_ids_for_connector("user-1", "org-1", "conn-1") == {}
    _assert_reads_no_share_grants(recorder)

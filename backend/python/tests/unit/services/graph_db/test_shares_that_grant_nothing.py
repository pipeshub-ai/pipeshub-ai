"""Domain, "anyone" and link shares grant no access, in both graph providers.

PipesHub deliberately does not turn a source's domain-wide, "anyone" or
"anyone with the link" share into access. Nothing writes those grants today,
but the queries that decide access still read them, so any such data left from
an older version made a record reachable by everyone in the org. These pin
that every access query ignores them: opening a record, the per-record
access check (``check_access``, which decides every open, reindex, delete and
listing), and the per-connector map search and chat use.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.permission import ORG_SHARE_PERMISSION_TYPES
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
    assert '"DOMAIN"' not in text and "'DOMAIN'" not in text, (
        "a query still grants access through a domain share"
    )
    for params in recorder.params():
        assert "@anyone" not in params


# ---- opening a record --------------------------------------------------------


_GRANTEES = ["user-key-1", "org-1"]
_ROW = {"id": "rec-1", "vrid": None, "connectorId": None, "indexingStatus": None, "isInternal": False}
# What an older access query returned for a record reachable only through an
# Anyone node: an entry with no source.
_LEGACY = [{"allAccess": [{"type": "ANYONE", "source": None, "role": "READER"}]}]


def _neo4j_open(*check_and_after: object) -> _Recorder:
    """The answers a record open reads on Neo4j: the user, the connector gate,
    the user's grants, then the batch check and whatever follows it."""
    return _Recorder(
        [{"u": {"id": "user-key-1"}}],
        [{"grantees": _GRANTEES, "gatedApps": []}],
        [{"grantees": _GRANTEES, "gatedApps": [], "grantSets": []}],
        *check_and_after,
    )


@pytest.mark.asyncio
async def test_neo4j_record_open_ignores_a_legacy_anyone_grant() -> None:
    # The batch check decides. It admits nothing here, so the record stays shut
    # whatever the query that names the access paths would have answered.
    recorder = _neo4j_open([{"items": []}], _LEGACY)
    provider = _neo4j(recorder)

    assert await provider.check_record_access_with_details("user-1", "org-1", "rec-1") is None
    assert len(recorder.calls) == 4, "the batch check did not run, or did not decide"
    _assert_reads_no_share_grants(recorder)


@pytest.mark.asyncio
async def test_neo4j_record_open_never_reports_a_legacy_anyone_grant() -> None:
    # Admitted by the batch check: the paths query runs, reads no Anyone grant,
    # and an entry with no source is not reported as the way in.
    recorder = _neo4j_open([{"items": [_ROW]}], _LEGACY)
    provider = _neo4j(recorder)
    provider.get_document = AsyncMock(return_value={"_key": "rec-1", "origin": "UPLOAD", "recordType": "OTHERS"})

    result = await provider.check_record_access_with_details("user-1", "org-1", "rec-1")

    assert result is not None
    assert [p["accessType"] for p in result["permissions"]] == ["CONNECTOR"]
    assert "allAccess" in recorder.calls[4][0], "the paths query did not run"
    _assert_reads_no_share_grants(recorder)


@pytest.mark.asyncio
async def test_arango_record_open_reads_no_anyone_grant() -> None:
    grants = [{"grantees": _GRANTEES, "gatedApps": [], "grants": []}]
    denied = _Recorder(grants, [])
    assert await _arango(denied).check_record_access_with_details("user-1", "org-1", "rec-1") is None
    assert len(denied.calls) == 2, "the batch check did not run, or did not decide"
    _assert_reads_no_share_grants(denied)

    # Admitted, the query that names the access paths runs too.
    admitted = _Recorder(grants, [{**_ROW, "ok": True, "walk": False}], [None])
    assert await _arango(admitted).check_record_access_with_details("user-1", "org-1", "rec-1") is not None
    assert "allAccess" in admitted.calls[2][0], "the paths query did not run"
    _assert_reads_no_share_grants(admitted)


# ---- the per-record access check (open, reindex, delete, listings) -------------


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_record_permission_check_reads_no_domain_or_anyone_grant(make) -> None:
    recorder = _Recorder([])
    provider = make(recorder)

    result = await provider.check_access("user-key-1", "org-1", node_ids=["rec-1"])

    assert result.node_ids == frozenset()
    assert len(recorder.calls) == 2, "the user's grants, then the check"
    _assert_reads_no_share_grants(recorder)
    # Organization-wide shares still count; only the three share kinds are dropped.
    for share_type in ORG_SHARE_PERMISSION_TYPES:
        assert f"'{share_type}'" in recorder.text() or f'"{share_type}"' in recorder.text()


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

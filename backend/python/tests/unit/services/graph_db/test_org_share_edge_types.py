"""Only a real org-wide share, not any organization edge, grants access, in both graph providers.

An org-wide share is written as an organization -> record (or record group)
PERMISSION edge typed "ORG" (connector permissions) or "ORGANIZATION" (a
service account's chat upload). Several access queries used to follow any
PERMISSION edge out of the organization, so an edge of another type, such as
a domain share, would have granted access to everyone in the org as well.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.permission import (
    ORG_SHARE_PERMISSION_TYPES,
    EntityType,
    Permission,
    PermissionType,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


class _Recorder:
    def __init__(self, *answers: object) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._answers = list(answers)

    async def __call__(self, query: str, *args: object, **kwargs: object) -> object:
        params = kwargs.get("parameters") or kwargs.get("bind_vars") or (args[0] if args else {}) or {}
        self.calls.append((query, dict(params)))
        return self._answers.pop(0) if self._answers else []


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


# An organization PERMISSION hop with no filter on the edge's type.
_NEO4J_ORG_HOP = re.compile(r"(?:Organization[^)]*|\(org2?)\)-\[(\w*):PERMISSION[^\]]*\]->")
_ARANGO_ORG_HOP = re.compile(r"FOR (\w+)(?:, (\w+))? IN 1\.\.1 ANY org\._id")


def _unfiltered_org_hops(query: str) -> list[str]:
    """Organization -> PERMISSION hops in a query that do not check the edge type."""
    loose: list[str] = []
    for match in _NEO4J_ORG_HOP.finditer(query):
        edge = match.group(1)
        following = query[match.end():match.end() + 400]
        typed_inline = re.search(r"type: ['\"]ORG['\"]", match.group(0))
        if not typed_inline and (not edge or f"{edge}.type IN $" not in following):
            loose.append(match.group(0))
    for match in _ARANGO_ORG_HOP.finditer(query):
        edge = match.group(2)
        following = query[match.end():match.end() + 400]
        filtered = edge and (
            f"{edge}.type IN @org_share_types" in following
            or re.search(rf"{edge}\.type == ['\"]ORG['\"]", following)
        )
        if not filtered:
            loose.append(match.group(0))
    return loose


def _assert_only_org_shares(recorder: _Recorder) -> None:
    assert recorder.calls, "no query was run, so nothing was checked"
    hops = 0
    for query, params in recorder.calls:
        hops += len(_NEO4J_ORG_HOP.findall(query)) + len(_ARANGO_ORG_HOP.findall(query))
        assert not _unfiltered_org_hops(query), (
            f"an organization edge is followed without checking its type: {_unfiltered_org_hops(query)}"
        )
        if "org_share_types" in query or "orgShareTypes" in query:
            types = params.get("org_share_types") or params.get("orgShareTypes")
            assert sorted(types) == sorted(ORG_SHARE_PERMISSION_TYPES)
    assert hops, "the query followed no organization edge, so the check measured nothing"


def test_the_accepted_types_are_what_an_org_share_writes() -> None:
    """The connector side writes the entity type; a domain share's type is not among them."""
    edge = Permission(type=PermissionType.READ, entity_type=EntityType.ORG).to_arango_permission(
        "org-1", "organizations", "rec-1", "records",
    )
    assert edge["type"] in ORG_SHARE_PERMISSION_TYPES
    assert EntityType.DOMAIN.value not in ORG_SHARE_PERMISSION_TYPES


@pytest.mark.asyncio
async def test_neo4j_record_open_grants_an_org_share_and_not_a_domain_edge() -> None:
    org_share = {"allAccess": [{"type": "ORGANIZATION", "source": {"id": "org-1"}, "role": "READER"}]}
    recorder = _Recorder([{"u": {"id": "user-key-1"}}], [org_share])
    provider = _neo4j(recorder)
    provider.get_document = AsyncMock(
        return_value={"_key": "rec-1", "origin": "UPLOAD", "recordType": "OTHERS", "recordName": "note"}
    )

    result = await provider.check_record_access_with_details("user-1", "org-1", "rec-1")

    assert result is not None, "an org-wide share must still open the record"
    _assert_only_org_shares(recorder)


@pytest.mark.asyncio
async def test_arango_record_open_follows_only_org_share_edges() -> None:
    recorder = _Recorder([None])
    provider = _arango(recorder)

    await provider.check_record_access_with_details("user-1", "org-1", "rec-1")
    _assert_only_org_shares(recorder)


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_search_map_follows_only_org_share_edges(make) -> None:
    recorder = _Recorder([])
    provider = make(recorder)

    await provider._get_virtual_ids_for_connector("user-1", "org-1", "conn-1")
    _assert_only_org_shares(recorder)


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
async def test_record_permission_check_follows_only_org_share_edges(make) -> None:
    recorder = _Recorder([])
    provider = make(recorder)

    await provider._check_record_permissions("rec-1", "user-key-1")
    _assert_only_org_shares(recorder)


@pytest.mark.asyncio
async def test_neo4j_linked_records_follow_only_org_share_edges() -> None:
    recorder = _Recorder([])
    provider = _neo4j(recorder)

    await provider.get_linked_records("rec-1", "org-1", "user-key-1", ["LINKED_TO"])
    _assert_only_org_shares(recorder)

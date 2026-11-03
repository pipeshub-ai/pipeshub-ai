"""Only a real org-wide share, not any organization edge, grants access, in both graph providers.

An org-wide share is written as an organization -> record (or record group)
PERMISSION edge typed "ORG" (connector permissions) or "ORGANIZATION" (a
service account's chat upload). Several access queries used to follow any
PERMISSION edge out of the organization, so an edge of another type, such as
a domain share, would have granted access to everyone in the org as well.

Access is decided by the batch check (``check_access``). It reads the user's
grants either as a list (``get_knowledge_hub_access_v3``) or from the node
(``_kh_grants_by_probe``), and the organization is one of the grantees in
both, so those hops carry the rule too.
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
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

# A PERMISSION hop onto a record or record group from one of the user's grantees,
# a list that holds the organization: written from the grantee, or from the node.
_NEO4J_GRANTEE_HOP = re.compile(
    r"\((?P<g1>\w+)\)-\[(?P<e1>\w*):PERMISSION\]->\(\w+:Record\|RecordGroup\)"
    r"|\(\w+\)<-\[(?P<e2>\w*):PERMISSION\]-\((?P<g2>\w+)\)"
)
_PERMISSION = CollectionNames.PERMISSION.value
_ARANGO_GRANTEE_HOP = re.compile(
    rf"FOR (?P<g1>\w+) IN kh_grantees\s+FOR (?P<e1>\w+) IN {_PERMISSION}\s+FILTER (?P=e1)\._from == (?P=g1)"
    rf"|FOR (?P<e2>\w+) IN {_PERMISSION}\s+FILTER (?P=e2)\._to == \w+\._id\s+"
    r"AND PARSE_IDENTIFIER\((?P=e2)\._from\)\.key IN @grantee_ids"
)
# Grantees that are never an organization: the groups, roles and teams of the
# source account a user authenticated a connector as.
_NEVER_AN_ORGANIZATION = {"kh_badge"}

_TYPES_CYPHER = "[" + ", ".join(f"'{t}'" for t in ORG_SHARE_PERMISSION_TYPES) + "]"
_TYPES_AQL = json.dumps(list(ORG_SHARE_PERMISSION_TYPES))
_ORGS = CollectionNames.ORGS.value


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
    for match in _NEO4J_GRANTEE_HOP.finditer(query):
        grantee = match.group("g1") or match.group("g2")
        edge = match.group("e1") or match.group("e2")
        if grantee in _NEVER_AN_ORGANIZATION:
            continue
        following = query[match.end():match.end() + 400]
        ruled = edge and (
            f"NOT {grantee}:Organization OR {edge}.type IN {_TYPES_CYPHER}" in following
            or f"{grantee}:Organization AND {edge}.type IN {_TYPES_CYPHER}" in following
        )
        if not ruled:
            loose.append(match.group(0))
    for match in _ARANGO_GRANTEE_HOP.finditer(query):
        edge = match.group("e1") or match.group("e2")
        grantee = match.group("g1") or f"{edge}._from"
        following = query[match.end():match.end() + 400]
        if f'NOT STARTS_WITH({grantee}, "{_ORGS}/") OR {edge}.type IN {_TYPES_AQL}' not in following:
            loose.append(match.group(0))
    return loose


def _org_hops(query: str) -> int:
    return sum(
        len(list(pattern.finditer(query)))
        for pattern in (_NEO4J_ORG_HOP, _ARANGO_ORG_HOP, _NEO4J_GRANTEE_HOP, _ARANGO_GRANTEE_HOP)
    )


def _assert_only_org_shares(recorder: _Recorder) -> None:
    assert recorder.calls, "no query was run, so nothing was checked"
    hops = 0
    for query, params in recorder.calls:
        hops += _org_hops(query)
        assert not _unfiltered_org_hops(query), (
            f"an organization edge is followed without checking its type: {_unfiltered_org_hops(query)}"
        )
        if "org_share_types" in query or "orgShareTypes" in query:
            types = params.get("org_share_types") or params.get("orgShareTypes")
            assert sorted(types) == sorted(ORG_SHARE_PERMISSION_TYPES)
    assert hops, "the query followed no organization edge, so the check measured nothing"


@pytest.mark.parametrize("query", [
    "MATCH (grantee)-[kh_ge:PERMISSION]->(granted:Record|RecordGroup) WHERE granted.connectorId IS NOT NULL",
    "MATCH (grantee)-[:PERMISSION]->(granted:Record|RecordGroup)",
    "WHERE EXISTS { (t)<-[kh_gp:PERMISSION]-(kh_g) WHERE kh_g.id IN $granteeIds }",
    "WHERE EXISTS { (t)<-[kh_gp:PERMISSION]-(kh_g) WHERE (NOT kh_g:Organization OR kh_gp.type IN ['DOMAIN']) }",
    f"FOR g IN kh_grantees FOR p IN {_PERMISSION} FILTER p._from == g FILTER STARTS_WITH(p._to, 'records/')",
    f"FOR kh_g IN {_PERMISSION} FILTER kh_g._to == t._id AND PARSE_IDENTIFIER(kh_g._from).key IN @grantee_ids LIMIT 1",
])
def test_a_grantee_hop_without_the_rule_is_found(query: str) -> None:
    assert _unfiltered_org_hops(query)


def test_the_accepted_types_are_what_an_org_share_writes() -> None:
    """The connector side writes the entity type; a domain share's type is not among them."""
    edge = Permission(type=PermissionType.READ, entity_type=EntityType.ORG).to_arango_permission(
        "org-1", "organizations", "rec-1", "records",
    )
    assert edge["type"] in ORG_SHARE_PERMISSION_TYPES
    assert "DOMAIN" not in ORG_SHARE_PERMISSION_TYPES


_GRANTEES = ["user-key-1", "org-1"]
_ROW = {"id": "rec-1", "vrid": None, "connectorId": None, "indexingStatus": None, "isInternal": False}


@pytest.mark.asyncio
async def test_neo4j_record_open_grants_an_org_share_and_not_a_domain_edge() -> None:
    org_share = {"allAccess": [{"type": "ORGANIZATION", "source": {"id": "org-1"}, "role": "READER"}]}
    recorder = _Recorder(
        [{"u": {"id": "user-key-1"}}],
        [{"grantees": _GRANTEES, "gatedApps": []}],
        [{"grantees": _GRANTEES, "gatedApps": [], "grantSets": []}],
        [{"items": [_ROW]}],
        [org_share],
    )
    provider = _neo4j(recorder)
    provider.get_document = AsyncMock(
        return_value={"_key": "rec-1", "origin": "UPLOAD", "recordType": "OTHERS", "recordName": "note"}
    )

    result = await provider.check_record_access_with_details("user-1", "org-1", "rec-1")

    assert result is not None, "an org-wide share must still open the record"
    assert result["permissions"][0]["accessType"] == "ORGANIZATION"
    _assert_only_org_shares(recorder)


@pytest.mark.asyncio
async def test_neo4j_record_open_reports_the_role_of_every_principal() -> None:
    """The details query answers one row per principal: the user and a linked source account."""
    own = {"allAccess": [{"type": "DIRECT", "source": {"id": "user-key-1"}, "role": "READER"}]}
    linked = {"allAccess": [{"type": "DIRECT", "source": {"id": "source-key-1"}, "role": "OWNER"}]}
    recorder = _Recorder(
        [{"u": {"id": "user-key-1"}}],
        [{"grantees": _GRANTEES, "gatedApps": []}],
        [{"grantees": _GRANTEES, "gatedApps": [], "grantSets": []}],
        [{"items": [_ROW]}],
        [own, linked],
    )
    provider = _neo4j(recorder)
    provider.get_document = AsyncMock(
        return_value={"_key": "rec-1", "origin": "UPLOAD", "recordType": "OTHERS", "recordName": "note"}
    )

    result = await provider.check_record_access_with_details("user-1", "org-1", "rec-1")

    assert "OWNER" in {p["relationship"] for p in result["permissions"]}


@pytest.mark.asyncio
async def test_arango_record_open_follows_only_org_share_edges() -> None:
    recorder = _Recorder(
        [{"grantees": _GRANTEES, "gatedApps": [], "grants": []}],
        [{**_ROW, "ok": True, "walk": False}],
        [None],
    )
    provider = _arango(recorder)

    assert await provider.check_record_access_with_details("user-1", "org-1", "rec-1") is not None
    assert "orgAccessPermissionEdge" in recorder.calls[2][0], "the record-open query did not run"
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
async def test_the_access_check_follows_only_org_share_edges(make) -> None:
    """The per-record check behind reindex, delete and every listing: the user's
    grants, and the grant on a record outside every App (a chat attachment)."""
    recorder = _Recorder([])
    provider = make(recorder)

    await provider.check_access("user-key-1", "org-1", node_ids=["rec-1"])

    assert len(recorder.calls) == 2, "the grants and the check itself"
    for query, _ in recorder.calls:
        assert _org_hops(query), "each of the two reads the organization's grants"
    _assert_only_org_shares(recorder)


@pytest.mark.asyncio
async def test_neo4j_grants_tested_from_the_node_follow_only_org_share_edges() -> None:
    """Browsing inside one connector tests a grant from the node instead of a list."""
    recorder = _Recorder([])
    provider = _neo4j(recorder)
    probe = {"connector_id": "conn-1", "grantees": _GRANTEES}

    await provider.check_access(
        "", "org-1", node_ids=["rec-1"],
        access={"grantee_ids": _GRANTEES, "gated_app_ids": ["conn-1"], "by_connector": {}, "probe": probe},
    )
    await provider.get_knowledge_hub_connector_grants("user-key-1", "org-1", "conn-1")
    await provider._kh_v3_chain_top_groups_by_probe("conn-1", "org-1", "rg-1", _GRANTEES, None)

    assert len(recorder.calls) == 3
    for query, _ in recorder.calls:
        assert _org_hops(query), "each of the three reads the organization's grants"
    assert "$grantedIds" not in recorder.calls[0][0], "the check still reads the grant list"
    _assert_only_org_shares(recorder)


@pytest.mark.asyncio
async def test_neo4j_linked_records_follow_only_org_share_edges() -> None:
    recorder = _Recorder([{"item": {"id": "rec-2"}}])
    provider = _neo4j(recorder)

    await provider.get_linked_records("rec-1", "org-1", "user-key-1", ["LINKED_TO"])

    assert len(recorder.calls) == 3, "the neighbours, then the access check on them"
    _assert_only_org_shares(recorder)

"""SYNC4 S7/S8 (MAINSTUDY test 7): every harness record read decides by main's one access check, on Neo4j and ArangoDB.

The H1 read gate (``RecordReadGate``: fetch_full_record, Slack threads) and ``TieredRecordAuthorizer`` (shared
citations, artifact sources) must answer exactly what ``IGraphDBProvider.check_access`` answers, on main's own v3
access graph. The deciding records are granted to the user directly yet refused by the check: a STRICT record
reached only through a link, and a RESTRICTED upload with no path; the old direct-edge tier admitted both.

Written test-first, before ``origin/main`` was merged: red until S7 and S8.
"""

from __future__ import annotations

import pytest

from .fixture_graph import ORG, USER_U
from .test_provider_v3_access_check import (  # noqa: F401 - the module fixture loads main's graph
    LINK_ONLY,
    UPLOAD_UNDER_AN_APP,
    USER_U_ID,
    access_graph,
)

pytestmark = pytest.mark.integration

DIRECT_GRANT_ONLY = {LINK_ONLY, UPLOAD_UNDER_AN_APP}


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


def _records(graph: dict) -> list[str]:
    return [n["id"] for n in graph["nodes"] if n["kind"] == "Record"]


class _FixtureRecords:
    """The backend, with the authorizer's typed record read answered from the fixture's documents: they carry the
    fields the access check reads, not every field main's typed ``Record`` requires. The decision stays the backend's."""

    def __init__(self, provider: object, graph: dict) -> None:
        self._provider = provider
        self._docs = {n["id"]: {"_key": n["id"], **n["props"]} for n in graph["nodes"] if n["kind"] == "Record"}

    async def get_typed_records_batch(self, record_ids: list[str]) -> dict[str, dict]:
        return {rid: self._docs[rid] for rid in record_ids if rid in self._docs}

    def __getattr__(self, name: str) -> object:
        return getattr(self._provider, name)


async def _oracle(provider, graph: dict) -> set[str]:
    readable = set((await provider.check_access(USER_U, ORG, node_ids=_records(graph))).node_ids)
    assert readable, "the fixture must leave the user something to read"
    assert not readable & DIRECT_GRANT_ONLY
    return readable


async def test_the_read_gate_answers_what_the_one_check_answers(access_graph, provider) -> None:  # noqa: F811
    from app.harness.knowledge.read_gate import RecordReadGate

    class _Off:
        async def enabled(self) -> bool:
            return False

    expected = await _oracle(provider, access_graph)
    gate = RecordReadGate(provider, ORG, USER_U_ID, publications=_Off())  # type: ignore[arg-type]
    decided = await gate.decide_many(_records(access_graph))
    assert {rid for rid, decision in decided.items() if decision == "own"} == expected


async def test_the_authorizer_has_no_direct_edge_tier(access_graph, provider) -> None:  # noqa: F811
    from app.services.artifact_registry.models import Actor
    from app.services.record_content.authorizer import TieredRecordAuthorizer

    expected = await _oracle(provider, access_graph)
    readable = await TieredRecordAuthorizer(_FixtureRecords(provider, access_graph)).readable(Actor(org_id=ORG, user_id=USER_U_ID), _records(access_graph))
    assert readable == expected


async def test_the_authorizer_decides_citations_the_same_way(access_graph, provider) -> None:  # noqa: F811
    from app.harness.contracts.generated.pipeshub.citation_source import (
        PipeshubRecordSource,
    )
    from app.services.artifact_registry.models import Actor
    from app.services.record_content.authorizer import TieredRecordAuthorizer

    expected = await _oracle(provider, access_graph)
    asked = _records(access_graph)
    sources = [PipeshubRecordSource.model_validate({"kind": "indexed", "recordId": rid}) for rid in asked]
    statuses = await TieredRecordAuthorizer(_FixtureRecords(provider, access_graph)).decide(Actor(org_id=ORG, user_id=USER_U_ID), sources)
    assert {rid for rid, status in zip(asked, statuses, strict=True) if status == "readable"} == expected

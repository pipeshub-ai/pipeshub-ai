"""H4 PB-1..PB-7 (H2 D-3): a record a `full` publication discloses, fetched by id outside the search map, carries
its publication into the record map, so its label names the publication and a shared view of the turn resolves it
as it resolves the same record found by search. Revoked or expired: fail closed.

The publications are the real service over an in-memory directory; the record map is filled by the real
``get_record`` from a fake blob store, as a fetch fills it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.knowledge.read_gate import RecordReadGate
from app.harness.labels import LabelSource, classify_record
from app.utils.fetch_full_record import _fetch_multiple_records_impl
from harness_core.knowledge.publications import Principal
from tests.support.knowledge_publications import NOW, ORG, publications_over, publish
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.harness.knowledge.service import KnowledgePublications

READER = "reader"
PEER = "peer"
STRANGER = "stranger"
CREATOR = "creator"
KB = "apps/kb-1"
KB2 = "apps/kb-2"


class Graph(InMemoryRecordAccess):
    """The shared record-access fake (``access`` seeds (user, record) ownership) and the record documents."""

    def __init__(self, access: Iterable[tuple[str, str]] = ()) -> None:
        super().__init__()
        for user_id, record_id in access:
            self.readable(user_id, ORG, record_id)
        self.config_service: object = None

    async def get_document(self, document_key: str, collection: str) -> dict[str, Any] | None:
        if collection != "records":
            return None
        return {
            "_key": document_key,
            "id": document_key,
            "virtualRecordId": f"v-{document_key}",
            "indexingStatus": "COMPLETED",
            "recordName": f"{document_key}.md",
            "recordType": "OTHERS",
            "connectorName": "KB",
            "connectorId": "kb-1",
            "orgId": ORG,
        }


class Blobs:
    class _Config:
        async def get_config(self, *_: object, **__: object) -> dict[str, Any]:
            return {}

    config_service = _Config()

    async def get_record_from_storage(self, virtual_record_id: str, org_id: str, **_: object) -> dict[str, Any]:
        return {"blocks": [], "virtual_record_id": virtual_record_id}


async def _fetch(
    graph: Graph, service: KnowledgePublications, records: dict[str, Any], *, user: str = READER,
    caller: str | None = None, ids: tuple[str, ...] = ("r1",),
) -> dict[str, Any]:
    gate = RecordReadGate(graph, ORG, user, caller_user_id=caller, publications=service)  # type: ignore[arg-type]
    return await _fetch_multiple_records_impl(
        list(ids), records, graph_provider=graph, blob_store=Blobs(), org_id=ORG, user_id=user, gate=gate,  # type: ignore[arg-type]
    )


def _entry(records: dict[str, Any], record_id: str = "r1") -> dict[str, Any]:
    return next(r for r in records.values() if r is not None and r.get("id") == record_id)


async def _readable(service: KnowledgePublications, user: str, entry: dict[str, Any]) -> bool:
    source = classify_record(entry)
    assert isinstance(source, LabelSource)
    readable = await service.readable_through(Principal(ORG, user), [(source.record_id, source.publication_id)])
    return (source.record_id, source.publication_id) in readable


async def _published(mode: str = "full", **kwargs: Any) -> tuple[Graph, KnowledgePublications]:  # noqa: ANN401
    graph = Graph(**kwargs)
    service = publications_over(graph, {KB: {"r1"}, KB2: {"r1"}})
    await publish(service, KB, {READER, PEER}, mode, pid="p-kb")  # type: ignore[arg-type]
    return graph, service


async def test_pb1_the_fetched_record_and_its_map_entry_carry_the_publication() -> None:
    graph, service = await _published()
    records: dict[str, Any] = {}
    result = await _fetch(graph, service, records)
    assert [r["id"] for r in result["records"]] == ["r1"]
    assert result["records"][0]["publicationId"] == "p-kb"
    entry = _entry(records)
    assert (entry["publicationId"], entry["publicationMode"]) == ("p-kb", "full")


async def test_pb2_its_label_names_the_publication_and_another_audience_member_reads_it_as_from_search() -> None:
    graph, service = await _published()
    records: dict[str, Any] = {}
    await _fetch(graph, service, records)
    fetched = _entry(records)
    assert classify_record(fetched) == LabelSource("indexed", record_id="r1", publication_id="p-kb")
    searched = {"id": "r1", "publicationId": "p-kb", "publicationMode": "full"}
    assert await _readable(service, PEER, fetched) is True
    assert await _readable(service, PEER, fetched) == await _readable(service, PEER, searched)


async def test_pb3_revoked_expired_or_outside_the_audience_fails_closed() -> None:
    graph, service = await _published()
    records: dict[str, Any] = {}
    await _fetch(graph, service, records)
    fetched = _entry(records)
    assert await _readable(service, STRANGER, fetched) is False
    _ = await service.directory.revoke(ORG, "p-kb", by="owner", now_ms=NOW - 1)
    assert await _readable(service, PEER, fetched) is False

    graph, expiring = await _published()
    await expiring.directory.erase_org(ORG)
    await publish(expiring, KB, {READER, PEER}, "full", pid="p-short", expires=NOW + 1)
    records = {}
    await _fetch(graph, expiring, records)
    later = publications_over(graph, {KB: {"r1"}}, now_ms=NOW + 2)
    later.directory = expiring.directory
    assert await _readable(later, PEER, _entry(records)) is False


async def test_pb4_own_access_carries_no_publication() -> None:
    graph, service = await _published(access={(READER, "r1")})
    records: dict[str, Any] = {}
    await _fetch(graph, service, records)
    assert "publicationId" not in _entry(records)


async def test_pb5_two_full_publications_name_the_lowest_id() -> None:
    graph, service = await _published()
    await publish(service, KB2, {READER}, "full", pid="p-a")
    records: dict[str, Any] = {}
    await _fetch(graph, service, records)
    assert _entry(records)["publicationId"] == "p-a"


async def test_pb6_a_service_account_agent_carries_its_callers_publication() -> None:
    graph = Graph(access={(CREATOR, "r1")})
    service = publications_over(graph, {KB: {"r1"}})
    await publish(service, KB, {READER}, "full", pid="p-caller")
    records: dict[str, Any] = {}
    result = await _fetch(graph, service, records, user=CREATOR, caller=READER)
    assert [r["id"] for r in result["records"]] == ["r1"]
    assert _entry(records)["publicationId"] == "p-caller"


async def test_pb7_a_map_entry_without_its_publication_is_stamped_before_it_is_served() -> None:
    graph, service = await _published()
    records: dict[str, Any] = {"v-r1": {"id": "r1", "record_name": "r1.md", "blocks": []}}
    result = await _fetch(graph, service, records)
    assert result["records"][0]["publicationId"] == "p-kb"
    assert (records["v-r1"]["publicationId"], records["v-r1"]["publicationMode"]) == ("p-kb", "full")

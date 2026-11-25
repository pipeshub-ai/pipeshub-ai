"""H1 FR-1..FR-8 (P2.13 FW-4): ``fetch_full_record`` decides access when it reads, for ids in the record map too.

The map is filled by retrieval, but also by a fetch of a model-named id, the Slack thread tool, history
attachments, a child run's records and the journal's citation deltas on resume; an entry also outlives a
revocation. These cases put entries in the map directly, as any of those writers would.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.harness.knowledge.read_gate import RecordReadGate
from app.utils.fetch_full_record import (
    NOT_INDEXED_YET,
    PUBLICATION_ANSWER_ONLY,
    STORAGE_ERROR,
    UNAVAILABLE,
    _fetch_multiple_records_impl,
)
from harness_core.knowledge.publications import ReadGrant
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from collections.abc import Set as AbstractSet

    from app.services.graph_db.interface.graph_db_provider import AccessCheck
    from harness_core.knowledge.publications import ReadDecision

ORG = "org-1"
READER = "user-reader"
CREATOR = "user-creator"
CALLER = "user-caller"


def _entry(record_id: str = "r1", **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"id": record_id, "record_name": f"{record_id}.pdf", "record_type": "FILE", "blocks": [], **extra}


class FakeGraph(InMemoryRecordAccess):
    """What the gate and the miss path read: the shared record-access fake (``access`` seeds (user, record)
    ownership; ``fail`` is a graph outage), its batched reads counted, and the record document."""

    def __init__(
        self,
        access: Iterable[tuple[str, str]] = (),
        fail: bool = False,
        documents: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        for user_id, record_id in access:
            self.readable(user_id, ORG, record_id)
        if fail:
            self.fail_with(ConnectionError("graph down"))
        self.batches: list[tuple[str, tuple[str, ...]]] = []
        self.documents = documents or {}
        self.config_service: object = None

    @property
    def asked(self) -> list[tuple[str, str]]:
        return [(user_id, record_id) for user_id, _org, record_id in self.asked_about]

    async def check_access(self, user_key: str, org_id: str, *, node_ids: Iterable[str] = (), **kwargs: Any) -> AccessCheck:  # noqa: ANN401
        self.batches.append((user_key, tuple(node_ids)))
        return await super().check_access(user_key, org_id, node_ids=node_ids, **kwargs)

    async def get_document(self, document_key: str, collection: str) -> dict[str, Any] | None:
        return self.documents.get(document_key)


@dataclass
class FakePublications:
    """``KnowledgePublications``' read surface: a live publication discloses a record to a user in a mode."""

    on: bool = True
    live: dict[tuple[str, str], str] = field(default_factory=dict[tuple[str, str], str])
    batches: int = 0

    async def enabled(self) -> bool:
        return self.on

    async def read_decision(self, user_id: str | None, org_id: str, record_id: str, *, own: bool) -> ReadDecision:
        if own:
            return "own"
        mode = self.live.get((user_id or "", record_id))
        if mode == "full":
            return "published_full"
        return "published_answer_only" if mode == "answer_only" else "denied"

    async def read_grants(
        self, user_id: str | None, org_id: str, record_ids: Sequence[str], *, own: AbstractSet[str]
    ) -> dict[str, ReadGrant]:
        self.batches += 1
        return {rid: ReadGrant(await self.read_decision(user_id, org_id, rid, own=rid in own)) for rid in record_ids}


async def _fetch(
    graph: FakeGraph,
    records: dict[str, Any],
    *,
    user: str | None = READER,
    caller: str | None = None,
    publications: FakePublications | None = None,
    ids: tuple[str, ...] = ("r1",),
) -> dict[str, Any]:
    gate = (
        RecordReadGate(graph, ORG, user, caller_user_id=caller, publications=publications or FakePublications(on=False))  # type: ignore[arg-type]
        if user is not None
        else None
    )
    return await _fetch_multiple_records_impl(
        list(ids), records, graph_provider=graph, org_id=ORG, user_id=user, gate=gate,  # type: ignore[arg-type]
    )


def _served(result: dict[str, Any]) -> list[str]:
    return [r["id"] for r in result.get("records", [])]


async def test_fr1_an_injected_entry_the_reader_cannot_read_is_not_served() -> None:
    graph = FakeGraph()
    result = await _fetch(graph, {"v1": _entry()})
    assert _served(result) == []
    assert result["unavailable_reasons"] == {"r1": UNAVAILABLE}
    assert (READER, "r1") in graph.asked


async def test_fr2_access_revoked_between_search_and_fetch_is_refused() -> None:
    graph = FakeGraph(access={(READER, "r1")})
    records = {"v1": _entry()}
    assert _served(await _fetch(graph, records)) == ["r1"]
    graph.revoke()
    result = await _fetch(graph, records)
    assert _served(result) == [] and result["unavailable_reasons"] == {"r1": UNAVAILABLE}


async def test_fr3_a_readable_entry_is_served_from_the_map_as_today() -> None:
    graph = FakeGraph(access={(READER, "r1")})
    graph.get_document = AsyncMock(side_effect=AssertionError("the map entry needs no graph read"))  # type: ignore[method-assign]
    blob = MagicMock()
    blob.get_record_from_storage = AsyncMock(side_effect=AssertionError("nor a blob read"))
    entry = _entry(blocks=[{"text": "body"}])
    gate = RecordReadGate(graph, ORG, READER, publications=FakePublications(on=False))  # type: ignore[arg-type]
    result = await _fetch_multiple_records_impl(
        ["r1"], {"v1": entry}, graph_provider=graph, blob_store=blob, org_id=ORG, user_id=READER, gate=gate,  # type: ignore[arg-type]
    )
    assert result["ok"] is True and result["records"][0]["blocks"] == [{"text": "body"}]
    assert result["records"][0]["virtual_record_id"] == "v1"
    assert result["not_available_ids"] == []


async def test_fr4_a_full_publication_serves_while_live_and_stops_at_revocation() -> None:
    graph = FakeGraph()
    publications = FakePublications(live={(READER, "r1"): "full"})
    records = {"v1": _entry(publicationId="p1", publicationMode="full")}
    assert _served(await _fetch(graph, records, publications=publications)) == ["r1"]
    publications.live.clear()
    result = await _fetch(graph, records, publications=publications)
    assert result["unavailable_reasons"] == {"r1": UNAVAILABLE}


async def test_fr5_an_answer_only_publication_is_never_read_in_full_and_revoked_reads_as_unavailable() -> None:
    graph = FakeGraph()
    publications = FakePublications(live={(READER, "r1"): "answer_only"})
    records = {"v1": _entry(publicationId="p1", publicationMode="answer_only")}
    result = await _fetch(graph, records, publications=publications)
    assert result["unavailable_reasons"] == {"r1": PUBLICATION_ANSWER_ONLY}
    publications.live.clear()
    result = await _fetch(graph, records, publications=publications)
    assert result["unavailable_reasons"] == {"r1": UNAVAILABLE}


async def test_fr5_own_access_gained_since_the_search_wins_over_the_publication() -> None:
    graph = FakeGraph(access={(READER, "r1")})
    publications = FakePublications(live={(READER, "r1"): "answer_only"})
    records = {"v1": _entry(publicationId="p1", publicationMode="answer_only")}
    assert _served(await _fetch(graph, records, publications=publications)) == ["r1"]


@pytest.mark.parametrize("in_map", [True, False])
async def test_fr6_a_service_account_agent_never_opens_what_its_caller_cannot_see(in_map: bool) -> None:
    graph = FakeGraph(
        access={(CREATOR, "r1")},
        documents={"r1": {"_key": "r1", "virtualRecordId": "v1", "indexingStatus": "COMPLETED"}},
    )
    records = {"v1": _entry()} if in_map else {}
    publications = FakePublications()
    result = await _fetch(graph, records, user=CREATOR, caller=CALLER, publications=publications)
    assert _served(result) == [] and result["unavailable_reasons"] == {"r1": UNAVAILABLE}
    assert (CALLER, "r1") in graph.asked
    # The caller's own access, or a publication to them, within the creator's: served (from the map).
    if in_map:
        graph.readable(CALLER, ORG, "r1")
        assert _served(await _fetch(graph, records, user=CREATOR, caller=CALLER, publications=publications)) == ["r1"]
        graph.revoke("r1", user=CALLER)
        publications.live[(CALLER, "r1")] = "full"
        assert _served(await _fetch(graph, records, user=CREATOR, caller=CALLER, publications=publications)) == ["r1"]
        # The creator's clearance bounds it: a publication to the caller alone opens nothing.
        graph.revoke()
        assert _served(await _fetch(graph, records, user=CREATOR, caller=CALLER, publications=publications)) == []


async def test_fr6_with_publications_off_the_agent_reads_as_its_creator_as_today() -> None:
    graph = FakeGraph(access={(CREATOR, "r1")})
    result = await _fetch(graph, {"v1": _entry()}, user=CREATOR, caller=CALLER, publications=FakePublications(on=False))
    assert _served(result) == ["r1"]


async def test_fr7_without_a_user_or_a_graph_nothing_is_served_from_the_map() -> None:
    graph = FakeGraph(access={(READER, "r1")})
    assert _served(await _fetch(graph, {"v1": _entry()}, user=None)) == []
    no_graph = await _fetch_multiple_records_impl(["r1"], {"v1": _entry()}, org_id=ORG, user_id=READER)
    assert no_graph["unavailable_reasons"] == {"r1": UNAVAILABLE}


async def test_fr8_a_failed_check_is_a_storage_error_and_serves_nothing() -> None:
    graph = FakeGraph(access={(READER, "r1")}, fail=True)
    result = await _fetch(graph, {"v1": _entry()})
    assert result["unavailable_reasons"] == {"r1": STORAGE_ERROR}


async def test_fr3_the_miss_path_is_unchanged_for_the_readers_own_record() -> None:
    graph = FakeGraph(
        access={(READER, "r2")},
        documents={"r2": {"_key": "r2", "virtualRecordId": "v2", "indexingStatus": "IN_PROGRESS"}},
    )
    result = await _fetch(graph, {}, ids=("r2",))
    assert result["unavailable_reasons"] == {"r2": NOT_INDEXED_YET}


async def test_rb5_a_fetch_of_many_ids_asks_the_graph_once() -> None:
    graph = FakeGraph(access={(READER, "r1"), (READER, "r2")})
    records = {"v1": _entry("r1"), "v2": _entry("r2"), "v3": _entry("r3")}
    publications = FakePublications(live={(READER, "r3"): "full"})
    result = await _fetch(graph, records, ids=("r1", "r2", "r3", "r1"), publications=publications)
    assert sorted(_served(result)) == ["r1", "r1", "r2", "r3"]
    assert graph.batches == [(READER, ("r1", "r2", "r3"))]
    assert publications.batches == 1


def _published_document(record_id: str) -> dict[str, Any]:
    return {"_key": record_id, "virtualRecordId": f"v-{record_id}", "indexingStatus": "COMPLETED"}


async def _fetch_by_id(graph: FakeGraph, publications: FakePublications, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
    async def download(_resolver: object, graph_record: dict[str, Any]) -> dict[str, Any]:
        return _entry(str(graph_record["_key"]))

    with patch("app.utils.fetch_full_record._RecordResolver._download", download):
        return await _fetch(graph, {}, publications=publications, **kwargs)


async def test_pf1_a_full_publication_opens_a_record_outside_the_map() -> None:
    graph = FakeGraph(documents={"r1": _published_document("r1")})
    result = await _fetch_by_id(graph, FakePublications(live={(READER, "r1"): "full"}))
    assert _served(result) == ["r1"]


async def test_pf2_an_answer_only_publication_outside_the_map_is_refused_typed() -> None:
    graph = FakeGraph(documents={"r1": _published_document("r1")})
    result = await _fetch_by_id(graph, FakePublications(live={(READER, "r1"): "answer_only"}))
    assert result["unavailable_reasons"] == {"r1": PUBLICATION_ANSWER_ONLY}


async def test_pf3_nothing_live_outside_the_map_is_not_available() -> None:
    graph = FakeGraph(documents={"r1": _published_document("r1")})
    result = await _fetch_by_id(graph, FakePublications(live={}))
    assert result["unavailable_reasons"] == {"r1": UNAVAILABLE}


async def test_pf4_a_service_account_agent_opens_it_only_within_its_creators_access() -> None:
    graph = FakeGraph(access={(CREATOR, "r1")}, documents={"r1": _published_document("r1")})
    publications = FakePublications(live={(CALLER, "r1"): "full"})
    assert _served(await _fetch_by_id(graph, publications, user=CREATOR, caller=CALLER)) == ["r1"]
    graph.revoke()
    result = await _fetch_by_id(graph, publications, user=CREATOR, caller=CALLER)
    assert result["unavailable_reasons"] == {"r1": UNAVAILABLE}

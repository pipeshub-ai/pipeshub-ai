"""H2 RB-1..RB-4: the read gate decides many ids with one ACL query per principal and one read of the publications.

The decisions are H1's (``test_fetch_full_record_access.py`` holds the fetch to them); what changes is the cost: a
search map of N ids is no longer N ACL round trips, nor N listings of every published container.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.knowledge.read_gate import RecordReadGate
from app.harness.knowledge.service import KnowledgePublications
from tests.support.knowledge_publications import NOW, ORG, Reads
from tests.support.knowledge_publications import publications_over as _publications
from tests.support.knowledge_publications import publish as _publish
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.graph_db.interface.graph_db_provider import AccessCheck
    from harness_core.knowledge.publications import ReadDecision

READER = "reader"
CREATOR = "creator"
CALLER = "caller"
KB = "apps/kb-1"
KB2 = "apps/kb-2"


class CountingGraph(InMemoryRecordAccess):
    """The shared record-access fake, each batched read counted as one round trip; ``access`` seeds (user, record)
    ownership. Asking one record at a time fails the test."""

    def __init__(self, access: Iterable[tuple[str, str]] = ()) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.config_service: object = None
        for user_id, record_id in access:
            self.readable(user_id, ORG, record_id)

    async def check_access(self, user_key: str, org_id: str, *, node_ids: Iterable[str] = (), **kwargs: Any) -> AccessCheck:  # noqa: ANN401
        assert org_id == ORG
        self.calls.append((user_key, tuple(node_ids)))
        return await super().check_access(user_key, org_id, node_ids=node_ids, **kwargs)

    async def check_record_access_with_details(
        self, user_id: str, org_id: str, record_id: str, transaction: str | None = None
    ) -> dict[str, Any] | None:
        raise AssertionError("the gate asked one record at a time")


@dataclass
class OffPublications:
    async def enabled(self) -> bool:
        return False

    async def read_grants(self, *_: object, **__: object) -> dict[str, ReadDecision]:
        raise AssertionError("publications are off")


async def test_rb1_n_ids_cost_one_acl_query_with_publications_off() -> None:
    graph = CountingGraph(access={(READER, "r1"), (READER, "r3")})
    gate = RecordReadGate(graph, ORG, READER, publications=OffPublications())  # type: ignore[arg-type]
    ids = [f"r{i}" for i in range(1, 21)]
    decided = await gate.decide_many(ids)
    assert graph.calls == [(READER, tuple(ids))]
    assert {rid for rid, d in decided.items() if d == "own"} == {"r1", "r3"}
    assert set(decided) == set(ids) and all(d in ("own", "denied") for d in decided.values())


async def test_rb2_a_service_account_agent_costs_one_query_per_principal() -> None:
    graph = CountingGraph(access={(CREATOR, "r1"), (CREATOR, "r2"), (CALLER, "r1")})
    reads = Reads()
    service = _publications(graph, {}, reads)
    gate = RecordReadGate(graph, ORG, CREATOR, caller_user_id=CALLER, publications=service)  # type: ignore[arg-type]
    decided = await gate.decide_many(["r1", "r2", "r3"])
    assert decided == {"r1": "own", "r2": "denied", "r3": "denied"}
    # The caller is asked only about what the creator may read.
    assert graph.calls == [(CREATOR, ("r1", "r2", "r3")), (CALLER, ("r1", "r2"))]


async def test_rb3_publications_are_read_once_for_every_id() -> None:
    graph = CountingGraph(access={(READER, "own")})
    reads = Reads()
    containers = {KB: {f"k{i}" for i in range(10)}, KB2: {"z1"}}
    service = _publications(graph, containers, reads)
    await _publish(service, KB, {READER}, "full", pid="p1")
    await _publish(service, KB2, {READER}, "answer_only", pid="p2")
    gate = RecordReadGate(graph, ORG, READER, publications=service)  # type: ignore[arg-type]
    ids = ["own", "z1", "nothing", *sorted(containers[KB])]
    decided = await gate.decide_many(ids)
    assert decided["own"] == "own" and decided["z1"] == "published_answer_only" and decided["nothing"] == "denied"
    assert all(decided[rid] == "published_full" for rid in containers[KB])
    assert len(graph.calls) == 1
    assert reads.principals == 1
    assert reads.listings == {KB: 1, KB2: 1}


async def test_rb3_all_own_reads_no_publication() -> None:
    graph = CountingGraph(access={(READER, "a"), (READER, "b")})
    reads = Reads()
    service = _publications(graph, {KB: {"a"}}, reads)
    gate = RecordReadGate(graph, ORG, READER, publications=service)  # type: ignore[arg-type]
    assert await gate.decide_many(["a", "b"]) == {"a": "own", "b": "own"}
    assert reads.principals == 0 and reads.listings == {}


ROWS: list[tuple[str, str | None, set[tuple[str, str]], ReadDecision]] = [
    # id, caller, ACL, expected (H1's rule)
    ("own", None, {(READER, "own")}, "own"),
    ("full", None, set(), "published_full"),
    ("answer", None, set(), "published_answer_only"),
    ("revoked", None, set(), "denied"),
    ("expired", None, set(), "denied"),
    ("answer", None, {(READER, "answer")}, "own"),
    ("none", None, set(), "denied"),
    ("full", CALLER, {(READER, "full")}, "published_full"),
    ("full", CALLER, set(), "denied"),
    ("own", CALLER, {(READER, "own"), (CALLER, "own")}, "own"),
    ("own", CALLER, {(READER, "own")}, "denied"),
]


@pytest.mark.parametrize(("record_id", "caller", "acl", "expected"), ROWS)
async def test_rb4_one_at_a_time_and_in_a_batch_decide_alike(
    record_id: str, caller: str | None, acl: set[tuple[str, str]], expected: ReadDecision
) -> None:
    async def gate() -> RecordReadGate:
        graph = CountingGraph(access=set(acl))
        containers = {KB: {"full", "revoked", "expired"}, KB2: {"answer"}}
        service = _publications(graph, containers, Reads())
        audience = {caller or READER}
        await _publish(service, KB, audience, "full", pid="p-full")
        await _publish(service, KB2, audience, "answer_only", pid="p-answer")
        return RecordReadGate(graph, ORG, READER, caller_user_id=caller, publications=service)  # type: ignore[arg-type]

    # Revoked and expired are their own containers' publications in this table.
    async def narrowed(g: RecordReadGate) -> RecordReadGate:
        service = g._publications  # pyright: ignore[reportPrivateUsage]
        assert isinstance(service, KnowledgePublications)
        if record_id in ("revoked", "expired"):
            for publication in await service.directory.list_for_org(ORG):
                if record_id == "revoked":
                    _ = await service.directory.revoke(ORG, publication.id, by="owner", now_ms=NOW - 1)
                else:
                    await service.directory.erase_org(ORG)
                    await _publish(service, KB, {caller or READER}, "full", pid="p-old", expires=NOW - 1)
                    break
        return g

    single = await narrowed(await gate())
    assert await single.decide(record_id) == expected
    batch = await narrowed(await gate())
    assert (await batch.decide_many([record_id, "none"]))[record_id] == expected


@pytest.mark.parametrize(("record_id", "caller", "acl", "expected"), ROWS)
async def test_pb9_decide_grants_decides_as_decide_many(
    record_id: str, caller: str | None, acl: set[tuple[str, str]], expected: ReadDecision
) -> None:
    graph = CountingGraph(access=set(acl))
    reads = Reads()
    service = _publications(graph, {KB: {"full"}, KB2: {"answer"}}, reads)
    audience = {caller or READER}
    await _publish(service, KB, audience, "full", pid="p-full")
    await _publish(service, KB2, audience, "answer_only", pid="p-answer")
    gate = RecordReadGate(graph, ORG, READER, caller_user_id=caller, publications=service)  # type: ignore[arg-type]
    if record_id in ("revoked", "expired"):
        return
    grants = await gate.decide_grants([record_id, "none"])
    assert grants[record_id].decision == expected
    named = {"published_full": "p-full", "published_answer_only": "p-answer"}.get(expected)
    assert grants[record_id].publication_id == named
    assert reads.principals <= 1 and all(count == 1 for count in reads.listings.values())


async def test_rb_a_failed_user_read_raises_rather_than_reading_as_no_access() -> None:
    """The gate's contract: a failed read raises, so the caller reports the read as failed instead of the records
    as ones the user may not see. The provider returns ``None`` for a failed lookup unless asked to raise."""

    class UserReadFails(CountingGraph):
        async def get_user_by_user_id(self, user_id: str | None = None, *_: object, raise_on_error: bool = False,
                                      **__: object) -> dict[str, Any] | None:
            if raise_on_error:
                raise ConnectionError("graph unavailable")
            return None

    gate = RecordReadGate(UserReadFails(access={(READER, "r1")}), ORG, READER, publications=OffPublications())  # type: ignore[arg-type]
    with pytest.raises(ConnectionError):
        await gate.decide_many(["r1"])

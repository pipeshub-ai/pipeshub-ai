"""Writers racing on one record: the conditional write lets the newest data win, whatever finishes last.

Each test names the counter-example it exists for; the mutant it must kill is in the slice's gate table.
"""

import pytest

from app.config.constants.arangodb import Connectors
from app.models.entities import Status
from app.models.record_metadata import (
    MetadataProvenance,
    MetadataValue,
    PropertyType,
    RecordMetadata,
)
from app.modules.record_metadata.projection import (
    CONTENT_CHANGED,
    CONTENTION,
    FAILED,
    FENCED_BY_GEN,
    SUPERSEDED,
    WRITTEN,
    Projection,
    ScopeUnionCache,
)

from .projection_world import (
    TICKETS,
    FakeGraph,
    add_ticket,
    defs,
    kb_doc,
    project,
    registry,
    set_ticket_status,
    snapshot,
    ticket,
)

pytestmark = pytest.mark.unit


def _meta(**values: object) -> RecordMetadata:
    return RecordMetadata(schema_id="org-1:kb-1:1", values={
        n: MetadataValue(value=v, provenance=MetadataProvenance.PROVIDED, source="push")  # type: ignore[arg-type]
        for n, v in values.items()
    })


async def test_older_stamp_loses_with_same_content_rev() -> None:
    """Run A read OPEN (stamp t1); the connector sets DONE (t2) without touching contentRev; run B projects
    DONE; run A then writes. The counter-example to a fence on contentRev equality: both runs carry the
    same revision, so only the stamp can tell A is older."""
    graph = FakeGraph()
    t = ticket(updated=1_000)
    add_ticket(graph, t, contentRev="rev-1")
    reg, _ = registry()
    cache = ScopeUnionCache()

    async def connector_and_run_b(n: int) -> None:
        if n != 1:
            return
        graph.before_write = None
        set_ticket_status(graph, t.id, Status.DONE, stamp=2_000)
        assert (await project(graph, reg, cache, t.id)).status == WRITTEN

    graph.before_write = connector_and_run_b
    a = await project(graph, reg, cache, t.id)

    assert a.status == SUPERSEDED
    assert graph.props(t.id)["status"] == "DONE" and graph.records[t.id]["propsSrc"] == 2_000


async def test_stamp_fence_does_not_depend_on_the_counter() -> None:
    """The counter and the stamp are separate conditions: a stored stamp newer than the writer's refuses
    it even when the counter matches (a record whose counter was never set)."""
    graph = FakeGraph()
    t = ticket(updated=1_000)
    add_ticket(graph, t, contentRev="rev-1", propsSrc=2_000, propsSet="old", propsGen=1)
    result = await project(graph, registry()[0], ScopeUnionCache(), t.id)
    assert result.status == SUPERSEDED and graph.writes == [] and graph.records[t.id]["propsSrc"] == 2_000


async def test_null_content_rev_still_fenced() -> None:
    """With artifact writing off the run has no contentRev, so nothing but the stamp orders the writers."""
    graph = FakeGraph()
    t = ticket(updated=1_000)
    add_ticket(graph, t, propsSrc=2_000, propsSet="old", propsGen=1)
    assert graph.records[t.id].get("contentRev") is None
    result = await project(graph, registry()[0], ScopeUnionCache(), t.id)
    assert result.status == SUPERSEDED and graph.writes == []


async def test_older_content_rev_loses() -> None:
    graph = FakeGraph()
    t = ticket(updated=1_000)
    add_ticket(graph, t, contentRev="rev-1")

    async def content_changes(n: int) -> None:
        graph.records[t.id]["contentRev"] = "rev-2"

    graph.before_write = content_changes
    result = await project(graph, registry()[0], ScopeUnionCache(), t.id)

    assert result.status == CONTENT_CHANGED and graph.writes == [] and graph.props(t.id) == {}


async def test_equal_stamp_rewrites() -> None:
    """The union grew at an unchanged stamp: rewriting at the same stamp is allowed."""
    reg, _ = registry()
    await reg.register("org-1", "kb-1", defs(("team", PropertyType.STRING)))
    cache = ScopeUnionCache()
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    meta = _meta(team="payments", tier="gold")
    await project(graph, reg, cache, doc["_key"], provided=meta)
    await reg.register("org-1", "kb-1", defs(("team", PropertyType.STRING)) + defs(("tier", PropertyType.STRING)))
    cache.invalidate("org-1", "kb-1")

    result = await project(graph, reg, cache, doc["_key"], provided=meta)

    assert result.status == WRITTEN and graph.props(doc["_key"]) == {"team": "payments", "tier": "gold"}
    assert graph.records[doc["_key"]]["propsSrc"] == 1_000


async def test_event_says_open_connector_wrote_done() -> None:
    """The event's record snapshot holds OPEN (stamp t1). The connector wrote DONE (t2) before index() ran,
    so the record document says t2. Stamping OPEN with t2 would make a stale value look current."""
    reg, _ = registry()
    await reg.register("org-1", "kb-1", defs(("status", PropertyType.STRING)))
    cache = ScopeUnionCache()
    graph = FakeGraph()
    event_doc = kb_doc(updated=1_000)                         # what the event carried: version t1
    graph.records[event_doc["_key"]] = kb_doc(updated=2_000)  # what index() reads: the connector's DONE (t2)
    ctx_record = snapshot(event_doc)

    stale = await project(
        graph, reg, cache, event_doc["_key"], record=ctx_record, record_doc=dict(graph.records[event_doc["_key"]]),
        provided=_meta(status="OPEN"),
    )

    assert stale.status == FAILED and stale.reason == "snapshot_older_than_record"
    assert graph.write_calls == 0 and graph.props(event_doc["_key"]) == {}

    # DONE's own event projects it.
    done = await project(
        graph, reg, cache, event_doc["_key"], record=snapshot(graph.records[event_doc["_key"]]),
        provided=_meta(status="DONE"),
    )
    assert done.status == WRITTEN
    assert graph.props(event_doc["_key"]) == {"status": "DONE"} and graph.records[event_doc["_key"]]["propsSrc"] == 2_000


async def test_equal_stamp_exactly_one_wins() -> None:
    """Two writers at one stamp (second-resolution source clocks): the counter lets exactly one apply per
    version, and the loser re-reads before it writes again."""
    graph = FakeGraph()
    t = ticket(updated=1_000)
    add_ticket(graph, t, contentRev="rev-1")
    reg, _ = registry()
    cache = ScopeUnionCache()
    b_results = []

    async def b_runs_first(n: int) -> None:
        if n == 1:
            graph.before_write = None
            b_results.append(await project(graph, reg, cache, t.id))

    graph.before_write = b_runs_first
    a = await project(graph, reg, cache, t.id)

    assert b_results[0].status == WRITTEN and b_results[0].attempts == 1
    # A's first statement (counter read before B wrote) was refused; its second applied after a re-read.
    assert len(graph.refused) == 1 and a.status == WRITTEN and a.attempts == 2
    assert graph.records[t.id]["propsVer"] == 2
    assert [w["controls"]["propsVer"] for w in graph.writes] == [1, 2]


async def test_a_writer_that_keeps_losing_the_counter_gives_up() -> None:
    graph = FakeGraph()
    t = ticket(updated=1_000)
    add_ticket(graph, t)

    async def someone_always_writes_first(n: int) -> None:
        graph.records[t.id]["propsVer"] = n  # a writer got in between, every time

    graph.before_write = someone_always_writes_first
    result = await project(graph, registry()[0], ScopeUnionCache(), t.id)

    assert result.status == CONTENTION and result.attempts == 3 and graph.writes == []


async def _two_pods() -> tuple[FakeGraph, str, object, object, ScopeUnionCache, ScopeUnionCache]:
    reg, _ = registry()
    await reg.register("org-1", "kb-1", defs(("status", PropertyType.STRING)))
    pod_b = ScopeUnionCache()  # caches generation 1
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    await pod_b.union_for(reg, "org-1", "kb-1", Connectors.KNOWLEDGE_BASE)
    await reg.register("org-1", "kb-1", defs(("status", PropertyType.STRING)) + defs(("team", PropertyType.STRING)))
    return graph, doc["_key"], reg, reg, ScopeUnionCache(), pod_b  # pod A is fresh: generation 2


@pytest.mark.parametrize("a_first", [True, False])
async def test_stale_union_pod_cannot_delete_newer_values(a_first: bool) -> None:
    """Pod A knows union generation 2 (``team`` joined); pod B's cache still holds generation 1. B's write,
    which has no ``team``, must not delete the value A projected."""
    graph, key, reg, _, pod_a, pod_b = await _two_pods()
    meta = _meta(status="OPEN", team="payments")

    async def run(cache: ScopeUnionCache) -> Projection:
        return await project(graph, reg, cache, key, provided=meta)

    if a_first:
        assert (await run(pod_a)).status == WRITTEN
        b = await run(pod_b)  # B reads the record after A wrote: the counter matches, only the generation refuses
        assert b.status == FENCED_BY_GEN
        assert graph.props(key) == {"status": "OPEN", "team": "payments"} and graph.records[key]["propsGen"] == 2
    else:
        assert (await run(pod_b)).status == WRITTEN
        assert graph.props(key) == {"status": "OPEN"}
        assert (await run(pod_a)).status == WRITTEN
        assert graph.props(key) == {"status": "OPEN", "team": "payments"} and graph.records[key]["propsGen"] == 2


async def test_two_pods_two_unions_converge_to_the_greater() -> None:
    graph, key, reg, _, pod_a, pod_b = await _two_pods()
    meta = _meta(status="OPEN", team="payments")
    for cache in (pod_b, pod_a, pod_b, pod_a, pod_b):
        await project(graph, reg, cache, key, provided=meta)
    assert graph.props(key) == {"status": "OPEN", "team": "payments"}


class _ConnectorMovesTheTicketAfterTheFirstRead(FakeGraph):
    """The connector updates the ticket (status and stamp) right after the builder's first record read."""

    moved = False

    async def get_document(self, key, collection, transaction=None, *, raise_on_error=False) -> dict | None:
        if collection == TICKETS and not self.moved:
            self.moved = True
            self.tickets[key]["status"] = Status.DONE.value
            self.records[key]["updatedAtTimestamp"] = 2_000
        return await super().get_document(key, collection, transaction, raise_on_error=raise_on_error)


async def test_values_and_stamp_come_from_the_same_version() -> None:
    """The record is read (stamp t1), the connector moves the ticket to DONE (t2), the tickets document is
    read (DONE): stamping DONE with t1 would let a later t1 write look newer than the data. The second
    record read sees t2 != t1, so the build starts over and DONE is written with t2."""
    graph = _ConnectorMovesTheTicketAfterTheFirstRead("arango")
    t = ticket()
    add_ticket(graph, t)

    result = await project(graph, registry()[0], ScopeUnionCache(), t.id)

    assert result.status == WRITTEN and result.attempts == 2
    assert graph.props(t.id)["status"] == "DONE" and graph.records[t.id]["propsSrc"] == 2_000

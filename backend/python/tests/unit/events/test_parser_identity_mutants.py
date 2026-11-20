"""Mutants of the identity rules, each killed by a scenario.

A scenario asserts the designed outcome. Run on the real code it passes; run with one rule altered (the monkeypatch below) it must fail. A
mutant that no scenario notices means a rule with no test behind it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.modules.indexing import keep_old_copy
from app.modules.pipeline import parser_identity as pi
from tests.support.parser_identity_world import T0, World
from tests.support.swap_world import FakeGraph, committer, ctx_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    monkeypatch.delenv("USE_PARSING_SERVICE", raising=False)
    monkeypatch.setattr("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock())
    pi.end_run_identity()


# ------------------------------------------------------------------------------------------------------------ scenarios


async def rename_runs_in_full() -> None:
    world = World()
    world.identified("inproc:txt@1")
    world.renamed_to_markdown()
    await world.run(world.event(mime="text/markdown", ext="md", name="notes.md"))
    assert world.parses == 1


async def stale_runs_in_full_and_is_not_adopted() -> None:
    world = World()
    world.identified("inproc:txt@1")
    world.state["lastIndexTimestamp"] = T0 + 500
    await world.run(world.event())
    assert world.parses == 1 and world.conditional_writes == []


async def an_identity_of_another_copy_is_not_known() -> None:
    world = World()
    world.identified("inproc:txt@1")
    world.state[pi.IDENTITY_VRID_FIELD] = "another"
    await world.run(world.event())
    assert world.parses == 1


async def a_replayed_older_event_is_judged_by_the_graph() -> None:
    world = World()
    world.renamed_to_markdown()
    world.identified("inproc:md@1")
    await world.run(world.event(mime="text/plain", ext="txt", name="notes.txt"))
    assert world.parses == 0


async def the_run_stores_the_route_that_parsed() -> None:
    world = World(node={"contentRev": "older"})
    world.renamed_to_markdown()
    await world.run(world.event(mime="text/plain", ext="txt", name="notes.txt"))
    assert world.state[pi.IDENTITY_FIELD] == "inproc:txt@1"


async def a_twin_of_another_parser_is_not_attached() -> None:
    world = World(node={"contentRev": "older", "virtualRecordId": "vr-twin"})
    world.renamed_to_markdown()
    twin = {
        "_key": "rec-9", "mimeType": "text/plain", "recordName": "a.txt", "recordType": "FILE", "virtualRecordId": "vr-twin",
        "lastIndexTimestamp": 7, pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 7, pi.IDENTITY_VRID_FIELD: "vr-twin",
    }
    assert await world.ep._twin_shares_parser(world.state, twin) is False


def a_queued_duplicate_of_another_parser_is_not_promoted() -> None:
    source = {
        "virtualRecordId": "v", "lastIndexTimestamp": 5, pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 5,
        pi.IDENTITY_VRID_FIELD: "v",
    }
    queued = {"_key": "q", "mimeType": "text/markdown", "recordName": "q.md", "recordType": "FILE"}
    assert pi.plan_promotions(source, [queued], {"q": {"extension": "md"}}, at=9, vrid="v") == {}


async def a_repointed_twin_gets_its_own_timestamp() -> None:
    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "Y", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1", "lastIndexTimestamp": 200,
              pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 200, pi.IDENTITY_VRID_FIELD: "Y", pi.IDENTITY_SOURCE_FIELD: "parsed"},
        "B": {"orgId": "o", "virtualRecordId": "X", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1", "lastIndexTimestamp": 100,
              pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 100, pi.IDENTITY_VRID_FIELD: "X", pi.IDENTITY_SOURCE_FIELD: "parsed"},
    })
    swap, _ = committer(g)
    await swap._repoint_twins(ctx_for("A", "Y"), keep_old_copy.SwapRun("A", "o", "X", "Y", True))
    assert g.records["B"][pi.IDENTITY_AT_FIELD] == 100 and pi.known_identity(g.records["B"]) == "inproc:txt@1"


async def a_re_indexed_twin_is_refused() -> None:
    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "Y", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1", "lastIndexTimestamp": 200,
              pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 200, pi.IDENTITY_VRID_FIELD: "Y"},
        "B": {"orgId": "o", "virtualRecordId": "X", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1", "lastIndexTimestamp": 100,
              pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 100, pi.IDENTITY_VRID_FIELD: "X"},
    })
    real = g.update_node_fields_if

    async def reindexed_first(key: str, collection: str, updates: dict, conditions: list, *a: Any, **k: Any) -> bool:  # noqa: ANN401
        if key == "B":
            g.records["B"]["lastIndexTimestamp"] = 150
        return await real(key, collection, updates, conditions, *a, **k)

    g.update_node_fields_if = reindexed_first  # type: ignore[method-assign]
    swap, _ = committer(g)
    await swap._repoint_twins(ctx_for("A", "Y"), keep_old_copy.SwapRun("A", "o", "X", "Y", True))
    assert g.records["B"]["virtualRecordId"] == "X"


SCENARIOS: dict[str, Callable[[], Awaitable[None] | None]] = {
    f.__name__: f for f in (
        rename_runs_in_full, stale_runs_in_full_and_is_not_adopted, an_identity_of_another_copy_is_not_known,
        a_replayed_older_event_is_judged_by_the_graph, the_run_stores_the_route_that_parsed, a_twin_of_another_parser_is_not_attached,
        a_queued_duplicate_of_another_parser_is_not_promoted, a_repointed_twin_gets_its_own_timestamp, a_re_indexed_twin_is_refused,
    )
}


async def play(name: str) -> None:
    outcome = SCENARIOS[name]()
    if outcome is not None:
        await outcome


# --------------------------------------------------------------------------------------------------------------- mutants


def _skip_ignores_identity(mp: pytest.MonkeyPatch) -> None:
    async def allow(self: Any, record: Any, record_id: str) -> tuple[bool, str | None]:  # noqa: ANN401
        return True, None

    from app.events.events import EventProcessor

    mp.setattr(EventProcessor, "_parser_gate", allow)


def _stale_counts_as_known(mp: pytest.MonkeyPatch) -> None:
    def known(record: Any) -> str | None:  # noqa: ANN401
        stored = record.get(pi.IDENTITY_FIELD)
        return stored if isinstance(stored, str) and stored else None

    mp.setattr(pi, "known_identity", known)


def _timestamp_without_the_copy(mp: pytest.MonkeyPatch) -> None:
    def known(record: Any) -> str | None:  # noqa: ANN401
        stored = record.get(pi.IDENTITY_FIELD)
        return stored if stored and record.get(pi.IDENTITY_AT_FIELD) == record.get("lastIndexTimestamp") else None

    mp.setattr(pi, "known_identity", known)


def _payload_used_in_the_skip(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(pi, "graph_parser_key", lambda record, file_doc: pi.parser_key("text/plain", "txt", "notes.txt", "FILE"))


def _identity_taken_from_the_graph_at_write_time(mp: pytest.MonkeyPatch) -> None:
    from app.events import events as events_module

    real = pi.parser_identity

    def from_graph(mime: Any, ext: Any, name: Any, rtype: Any, *, pipeline: str) -> str | None:  # noqa: ANN401
        return real("text/markdown", "md", "notes.md", rtype, pipeline=pipeline)

    mp.setattr(events_module.parser_identity, "parser_identity", from_graph)


def _attach_ignores_the_identity(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(pi, "twin_may_share", lambda *a, **k: True)


def _promotion_without_the_check(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(pi, "promotion_fields", lambda *a, **k: {})


def _repoint_without_the_timestamp_condition(mp: pytest.MonkeyPatch) -> None:
    from tests.support import swap_world

    real = swap_world.FakeGraph.update_node_fields_if

    async def relaxed(self: Any, key: str, collection: str, updates: dict, conditions: list, *a: Any, **k: Any) -> bool:  # noqa: ANN401
        return await real(self, key, collection, updates, [c for c in conditions if c.field != "lastIndexTimestamp"], *a, **k)

    mp.setattr(swap_world.FakeGraph, "update_node_fields_if", relaxed)


def _timestamp_copied_from_the_source(mp: pytest.MonkeyPatch) -> None:
    real = pi.indexed_parser_fields

    def copied(identity: str | None, source: str, *, at: int, vrid: str | None) -> dict[str, Any]:
        return real(identity, source, at=200, vrid=vrid)

    mp.setattr(pi, "indexed_parser_fields", copied)


MUTANTS: list[tuple[str, Callable[[pytest.MonkeyPatch], None], str]] = [
    ("the skip ignores the identity", _skip_ignores_identity, "rename_runs_in_full"),
    ("a stale identity counts as known (adopted)", _stale_counts_as_known, "stale_runs_in_full_and_is_not_adopted"),
    ("a timestamp without the copy it was written with", _timestamp_without_the_copy, "an_identity_of_another_copy_is_not_known"),
    ("the payload, not the graph, is compared in the skip", _payload_used_in_the_skip, "a_replayed_older_event_is_judged_by_the_graph"),
    ("the identity is the graph's at write time", _identity_taken_from_the_graph_at_write_time, "the_run_stores_the_route_that_parsed"),
    ("the attach ignores the identity", _attach_ignores_the_identity, "a_twin_of_another_parser_is_not_attached"),
    ("a promotion without the identity check", _promotion_without_the_check, "a_queued_duplicate_of_another_parser_is_not_promoted"),
    ("the re-point without the lastIndexTimestamp condition", _repoint_without_the_timestamp_condition, "a_re_indexed_twin_is_refused"),
    ("the re-point copies the source's timestamp", _timestamp_copied_from_the_source, "a_repointed_twin_gets_its_own_timestamp"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_the_scenario_passes_on_the_real_rules(name: str) -> None:
    await play(name)


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "apply", "scenario"), MUTANTS, ids=[m[0] for m in MUTANTS])
async def test_the_mutant_is_killed(label: str, apply: Callable[[pytest.MonkeyPatch], None], scenario: str, monkeypatch: pytest.MonkeyPatch) -> None:
    apply(monkeypatch)
    with pytest.raises(AssertionError):
        await play(scenario)

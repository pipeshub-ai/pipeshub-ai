"""The twin re-point of a forced run moves only a twin shown to hold a copy of the same parser, and moves its identity with it.

The committer's own identity must be known for this very commit; a twin's identity is written with the twin's own timestamp (the move does
not touch it), and the write is refused for a twin that was re-indexed since it was read.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.indexing import keep_old_copy
from app.modules.pipeline import parser_identity as pi
from tests.support.swap_world import FakeGraph, committer, ctx_for

IDENTITY = "inproc:txt@1"


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    monkeypatch.setattr("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock())


def rec(vrid: str, status: str = "COMPLETED", *, identity: str | None = None, at: int | None = 100, stamp: int = 100, source: str = "parsed", **more: object) -> dict:
    doc = {"orgId": "o", "virtualRecordId": vrid, "indexingStatus": status, "md5Checksum": "m", "connectorId": "k1", "lastIndexTimestamp": stamp, **more}
    if identity:
        doc.update({pi.IDENTITY_FIELD: identity, pi.IDENTITY_AT_FIELD: at, pi.IDENTITY_VRID_FIELD: vrid, pi.IDENTITY_SOURCE_FIELD: source})
    return doc


def run() -> keep_old_copy.SwapRun:
    return keep_old_copy.SwapRun("A", "o", "X", "Y", True)


def world(*, committer_source: str = "parsed", **twins: dict) -> FakeGraph:
    return FakeGraph({"A": rec("Y", identity=IDENTITY, at=200, stamp=200, source=committer_source), **twins})


@pytest.mark.asyncio
async def test_a_matching_twin_moves_with_its_identity_written_with_its_own_timestamp() -> None:
    g = world(committer_source="adopted", B=rec("X", identity=IDENTITY, at=100, stamp=100))
    swap, _ = committer(g)

    await swap._repoint_twins(ctx_for("A", "Y"), run())

    b = g.records["B"]
    assert b["virtualRecordId"] == "Y"
    assert b[pi.IDENTITY_AT_FIELD] == b["lastIndexTimestamp"] == 100  # its own, not the committer's 200
    assert b[pi.IDENTITY_VRID_FIELD] == "Y"
    assert b[pi.IDENTITY_SOURCE_FIELD] == "adopted"  # the committer's label is carried, never relabelled twin
    assert pi.known_identity(b) == IDENTITY


@pytest.mark.asyncio
async def test_a_twin_of_the_parsed_source_is_labelled_twin() -> None:
    g = world(B=rec("X", identity=IDENTITY))
    swap, _ = committer(g)
    await swap._repoint_twins(ctx_for("A", "Y"), run())
    assert g.records["B"][pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_TWIN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "twin",
    [
        rec("X"),                                               # no identity
        rec("X", identity=IDENTITY, at=99),                     # stale: the copy was rebuilt after the parse
        rec("X", identity="inproc:md@1"),                       # known, another parser
    ],
    ids=["unknown", "stale", "different"],
)
async def test_a_twin_that_is_unknown_stale_or_different_is_not_moved(twin: dict) -> None:
    g = world(B=twin)
    swap, _ = committer(g)

    await swap._repoint_twins(ctx_for("A", "Y"), run())

    assert g.records["B"]["virtualRecordId"] == "X"  # keeps the old copy, which survives while it holds it


@pytest.mark.asyncio
async def test_twins_move_as_before_when_the_committers_own_identity_is_not_known_for_this_commit() -> None:
    """With the flag off at the commit, ``me`` still holds the previous copy's identity: it is not known for ``Y``."""
    g = FakeGraph({"A": rec("Y", identity=IDENTITY, at=200, stamp=200), "B": rec("X", identity=IDENTITY)})
    g.records["A"][pi.IDENTITY_VRID_FIELD] = "X"
    swap, _ = committer(g)

    await swap._repoint_twins(ctx_for("A", "Y"), run())

    # not gated by an identity the committer does not have: the twin moves as before, and its own identity goes stale (vrid bound)
    assert g.records["B"]["virtualRecordId"] == "Y"
    assert pi.known_identity(g.records["B"]) is None


@pytest.mark.asyncio
async def test_a_twin_re_indexed_between_the_read_and_the_write_is_refused() -> None:
    g = world(B=rec("X", identity=IDENTITY))
    swap, _ = committer(g)
    real = g.update_node_fields_if

    async def reindexed_first(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        if key == "B":
            g.records["B"]["lastIndexTimestamp"] = 150
        return await real(key, collection, updates, conditions, *a, **k)

    g.update_node_fields_if = reindexed_first  # type: ignore[method-assign]
    await swap._repoint_twins(ctx_for("A", "Y"), run())

    assert g.records["B"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_the_undo_gives_back_all_the_identity_fields_and_unsets_what_the_twin_did_not_have() -> None:
    g = world(B=rec("X", identity=IDENTITY, source="adopted"))
    before = {k: g.records["B"][k] for k in pi.IDENTITY_FIELDS}
    swap, _ = committer(g)
    real = g.update_node_fields_if

    async def later_run_commits(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        moved = await real(key, collection, updates, conditions, *a, **k)
        if key == "B" and moved and updates.get("virtualRecordId") == "Y":
            g.records["A"]["virtualRecordId"] = "Z"
        return moved

    g.update_node_fields_if = later_run_commits  # type: ignore[method-assign]
    await swap._repoint_twins(ctx_for("A", "Y"), run())

    assert g.records["B"]["virtualRecordId"] == "X"
    assert {k: g.records["B"][k] for k in pi.IDENTITY_FIELDS} == before


@pytest.mark.asyncio
async def test_an_identity_that_the_twin_did_not_have_is_unset_by_the_undo() -> None:
    """Only reachable through a twin whose identity was read as known and then removed: the undo leaves no field it added."""
    g = world(B=rec("X", identity=IDENTITY))
    swap, _ = committer(g)
    real = g.update_node_fields_if

    async def later_run_commits(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        moved = await real(key, collection, updates, conditions, *a, **k)
        if key == "B" and moved and updates.get("virtualRecordId") == "Y":
            g.records["A"]["virtualRecordId"] = "Z"
        return moved

    g.update_node_fields_if = later_run_commits  # type: ignore[method-assign]
    await swap._repoint_twins(ctx_for("A", "Y"), run())
    assert g.records["B"][pi.IDENTITY_VRID_FIELD] == "X"

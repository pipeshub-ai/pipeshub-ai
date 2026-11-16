"""Per-cell stats files: claims, merge, staleness, dead loops, and the indexing ``/health`` answer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.capacity.cells import (
    _OFD,
    STALE_TICKS,
    STARTUP_GRACE_S,
    claim_cell,
    read_cells,
    write_cell,
)

pytestmark = pytest.mark.skipif(not _OFD, reason="cell claims need Linux open-file-description locks")


def _write(claim, **kw) -> None:
    kw.setdefault("loop_failure", None)
    kw.setdefault("tick_s", 5.0)
    write_cell(claim, {"heavy": 1}, **kw)


def test_claims_are_distinct_and_bounded(tmp_path) -> None:
    claims = [claim_cell(tmp_path, 3) for _ in range(3)]
    assert [c.index for c in claims] == [0, 1, 2]
    assert claim_cell(tmp_path, 3) is None
    claims[1].release()
    again = claim_cell(tmp_path, 3)
    assert again is not None and again.index == 1


def test_merge_lists_every_held_cell_in_order(tmp_path) -> None:
    a, b = claim_cell(tmp_path, 2), claim_cell(tmp_path, 2)
    _write(b, now_ms=1_000)
    _write(a, now_ms=1_000)
    merged = read_cells(tmp_path, 2, now_ms=2_000)
    assert merged.healthy
    assert [c["cell"] for c in merged.cells] == [0, 1]
    assert merged.cells[0]["stats"] == {"heavy": 1}


def test_a_stale_file_of_a_live_cell_is_unhealthy(tmp_path) -> None:
    a = claim_cell(tmp_path, 1)
    _write(a, now_ms=0)
    assert read_cells(tmp_path, 1, now_ms=STALE_TICKS * 5000).healthy
    merged = read_cells(tmp_path, 1, now_ms=STALE_TICKS * 5000 + 1)
    assert not merged.healthy and merged.cells[0]["reason"] == "stale"


def test_a_dead_loop_is_unhealthy(tmp_path) -> None:
    a = claim_cell(tmp_path, 1)
    _write(a, now_ms=10, loop_failure="SystemExit")
    merged = read_cells(tmp_path, 1, now_ms=20)
    assert not merged.healthy and merged.cells[0]["loop_failure"] == "SystemExit"


def test_a_file_whose_slot_is_free_is_ignored_and_the_next_claim_removes_it(tmp_path) -> None:
    a = claim_cell(tmp_path, 1)
    _write(a, now_ms=0)
    a.release()
    merged = read_cells(tmp_path, 1, now_ms=10**9)
    assert merged.healthy and merged.cells == []
    assert claim_cell(tmp_path, 1) is not None
    assert not (tmp_path / "0.json").exists()


def test_a_cell_that_has_not_written_yet_is_not_counted(tmp_path) -> None:
    claim_cell(tmp_path, 2)
    assert read_cells(tmp_path, 2).cells == []


@pytest.mark.parametrize("content", ["not json", "{}", '{"ts_ms": "x"}', "x" * (2 << 20)])
def test_an_unreadable_file_of_a_live_cell_is_unhealthy(tmp_path, content: str) -> None:
    claim_cell(tmp_path, 1)
    (tmp_path / "0.json").write_text(content)
    merged = read_cells(tmp_path, 1)
    assert not merged.healthy and merged.cells[0]["reason"] == "unreadable"


@pytest.mark.parametrize(
    "content",
    [
        '{"ts_ms":0,"tick_s":"nan"}',
        '{"ts_ms":0,"tick_s":NaN}',
        '{"ts_ms":0,"tick_s":Infinity}',
        '{"ts_ms":0,"tick_s":0}',
        '{"ts_ms":0,"tick_s":-5}',
        '{"ts_ms":0,"tick_s":1e300}',
        '{"ts_ms":1e309}',
        '{"ts_ms":NaN}',
        '{"ts_ms":true}',
        '{"ts_ms":"12"}',
    ],
)
def test_non_finite_or_out_of_range_numbers_are_unreadable_not_healthy_or_a_500(tmp_path, content: str) -> None:
    claim_cell(tmp_path, 1)
    (tmp_path / "0.json").write_text(content)
    merged = read_cells(tmp_path, 1, now_ms=10**12)
    assert not merged.healthy and merged.cells[0]["reason"] == "unreadable"


def _age_claim(directory, seconds: float) -> None:
    import os
    import time

    past = time.time() - seconds
    os.utime(directory / "0.lock", (past, past))


def test_a_cell_that_never_writes_is_unhealthy_after_the_grace(tmp_path) -> None:
    claim_cell(tmp_path, 1)
    _age_claim(tmp_path, STARTUP_GRACE_S - 60)
    assert read_cells(tmp_path, 1).cells == []  # still starting
    _age_claim(tmp_path, STARTUP_GRACE_S + 60)
    merged = read_cells(tmp_path, 1)
    assert not merged.healthy and merged.cells[0]["reason"] == "no_first_write"


def test_a_reclaimed_index_starts_its_grace_again(tmp_path) -> None:
    first = claim_cell(tmp_path, 1)
    _age_claim(tmp_path, STARTUP_GRACE_S + 60)
    first.release()
    claim_cell(tmp_path, 1)  # the lock file is old, the claim is not
    assert read_cells(tmp_path, 1).cells == []


def test_the_first_write_ends_the_wait(tmp_path) -> None:
    claim = claim_cell(tmp_path, 1)
    _age_claim(tmp_path, STARTUP_GRACE_S + 60)
    _write(claim)
    assert read_cells(tmp_path, 1).healthy


def _request(claim) -> MagicMock:
    request = MagicMock()
    request.app.state = MagicMock()
    del request.app.state.governor
    del request.app.state.pipeline_runtime
    request.app.state.cell_claim = claim
    return request


async def _health(claim) -> object:
    from app.indexing_main import health_check

    container = MagicMock()
    container.worker_loop = None
    container.kafka_consumers = []
    with (
        patch("app.indexing_main.container", container),
        patch("app.indexing_main.get_epoch_timestamp_in_ms", return_value=1),
        patch("app.indexing_main._indexing_worker_count", return_value=2),
    ):
        return await health_check(_request(claim))


async def test_health_serves_every_cell_and_503s_on_a_stale_one(tmp_path) -> None:
    a, b = claim_cell(tmp_path, 2), claim_cell(tmp_path, 2)
    import time

    now = int(time.time() * 1000)
    _write(a, now_ms=now)
    _write(b, now_ms=now)
    ok = await _health(a)
    body = json.loads(ok.body)
    assert ok.status_code == 200 and [c["cell"] for c in body["cells"]["cells"]] == [0, 1]
    # the same body from the other worker
    other = json.loads((await _health(b)).body)
    assert other["cells"]["cells"][1]["stats"] == body["cells"]["cells"][1]["stats"]
    _write(b, now_ms=now - 10 * 60 * 1000)
    bad = await _health(a)
    assert bad.status_code == 503 and json.loads(bad.body)["status"] == "unhealthy"


async def test_health_503_when_another_cells_loop_died(tmp_path) -> None:
    a, b = claim_cell(tmp_path, 2), claim_cell(tmp_path, 2)
    import time

    now = int(time.time() * 1000)
    _write(a, now_ms=now)
    _write(b, now_ms=now, loop_failure="SystemExit")
    assert (await _health(a)).status_code == 503


async def test_health_without_cells_is_unchanged() -> None:
    result = await _health(None)
    assert result.status_code == 200 and "cells" not in json.loads(result.body)

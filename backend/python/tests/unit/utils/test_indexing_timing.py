"""Per-record indexing stage timings (app.utils.indexing_timing)."""

import asyncio
import logging

import pytest

from app.utils import indexing_timing as timing


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("test_indexing_timing")
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDEXING_TIMING_LOGS", raising=False)
    monkeypatch.delenv("INDEXING_SLOW_STEP_SECONDS", raising=False)


def test_phases_split_wait_from_hold_and_parse() -> None:
    timeline = timing.RecordTimeline("r1", "newRecord", "light", started=100.0)
    timeline.mark(timing.ADMITTED, at=103.0)
    timeline.mark(timing.PARSE_REQUESTED, at=104.0)
    timeline.mark(timing.PARSE_ADMITTED, at=110.0)
    timeline.mark(timing.PARSED, at=112.0)
    timeline.mark(timing.RELEASED, at=150.0)

    assert timeline.phases(now=151.0) == {
        "wait_slot": 3.0,
        "held": 47.0,
        "pre_parse": 1.0,
        "parse_wait": 6.0,
        "parse": 2.0,
        "post_parse": 38.0,
    }


def test_a_parse_cut_short_still_reports_its_time_in_the_parser() -> None:
    # Admitted to the parser, then timed out or cancelled before PARSED.
    timeline = timing.RecordTimeline("r1", started=100.0)
    timeline.mark(timing.ADMITTED, at=101.0)
    timeline.mark(timing.PARSE_REQUESTED, at=102.0)
    timeline.mark(timing.PARSE_ADMITTED, at=105.0)
    timeline.mark(timing.RELEASED, at=165.0)

    assert timeline.phases(now=166.0) == {
        "wait_slot": 1.0,
        "held": 64.0,
        "pre_parse": 1.0,
        "parse_wait": 3.0,
        "parse": 60.0,
    }


def test_a_boundary_counts_only_its_first_crossing() -> None:
    timeline = timing.RecordTimeline("r1", started=0.0)
    timeline.mark(timing.ADMITTED, at=1.0)
    timeline.mark(timing.RELEASED, at=5.0)
    # The wrapper's finally marks RELEASED again after INDEXING_COMPLETE did.
    timeline.mark(timing.RELEASED, at=9.0)

    assert timeline.phases(now=10.0)["held"] == 4.0


def test_a_record_never_admitted_only_reports_its_wait() -> None:
    timeline = timing.RecordTimeline("r1", started=0.0)

    assert timeline.phases(now=7.5) == {"wait_slot": 7.5}


def test_summary_ranks_stages_by_time_and_counts_concurrent_batches() -> None:
    timeline = timing.RecordTimeline("rec-9", "reindexRecord", "light", started=0.0)
    timeline.mark(timing.ADMITTED, at=0.5)
    timeline.add("graph.read", 0.2)
    for seconds in (10.0, 30.0, 20.0):
        timeline.add("embed.dense", seconds)
    timeline.add("blob.write", 1.0)

    line = timeline.summary("ok", now=40.0)

    assert line.startswith("⏱ Indexing timings for record rec-9 (reindexRecord, light, ok): total=40.0s")
    assert "wait_slot=0.5s held=39.5s" in line
    assert "embed.dense=60.0s/3(max 30.0s) blob.write=1.0s graph.read=0.2s" in line
    assert "slow steps" not in line


@pytest.mark.asyncio
async def test_stages_in_spawned_tasks_add_to_the_records_timeline(logger) -> None:
    timeline, token = timing.begin_record("r1", "newRecord", "light")
    try:
        async def batch() -> None:
            with timing.stage("embed.dense"):
                await asyncio.sleep(0)

        await asyncio.gather(*(batch() for _ in range(5)))
        assert timing.current_timeline() is timeline
    finally:
        timing.end_record(timeline, token, outcome="ok", logger=logger)

    count, total, _ = timeline.stage_totals()["embed.dense"]
    assert count == 5
    assert total >= 0
    assert timing.current_timeline() is None


def test_a_stage_that_raises_is_still_timed() -> None:
    timeline, token = timing.begin_record("r1")
    try:
        with pytest.raises(RuntimeError):
            with timing.stage("graph.read"):
                raise RuntimeError("boom")
    finally:
        timing.end_record(timeline, token, outcome="failed")

    assert timeline.stage_totals()["graph.read"][0] == 1


def test_slow_step_warns_a_bounded_number_of_times_but_counts_all(monkeypatch, logger, caplog) -> None:
    monkeypatch.setenv("INDEXING_SLOW_STEP_SECONDS", "5")
    timeline, token = timing.begin_record("rec-slow")
    try:
        with caplog.at_level(logging.WARNING, logger=logger.name):
            for _ in range(6):
                timing.record_stage("embed.dense", 7.0, detail="batch of 50 texts", logger=logger)
            timing.record_stage("embed.dense", 1.0, logger=logger)
    finally:
        timing.end_record(timeline, token, outcome="ok", logger=logger)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3
    assert "Slow indexing step embed.dense took 7.0s (threshold 5s) for record rec-slow: batch of 50 texts" in warnings[0].getMessage()
    assert timeline.stage_totals()["embed.dense"][0] == 7
    timeline.mark(timing.ADMITTED, at=timeline.started)
    assert "slow steps: embed.dense:6" in timeline.summary("ok")


def test_slow_step_outside_a_record_still_warns(monkeypatch, logger, caplog) -> None:
    monkeypatch.setenv("INDEXING_SLOW_STEP_SECONDS", "1")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        timing.record_stage("graph.enrich", 2.0, logger=logger)

    assert any("graph.enrich took 2.0s" in r.getMessage() for r in caplog.records)


def test_summary_is_logged_once_per_admitted_record(logger, caplog) -> None:
    admitted, token = timing.begin_record("r-admitted")
    admitted.mark(timing.ADMITTED)
    with caplog.at_level(logging.INFO, logger=logger.name):
        timing.end_record(admitted, token, outcome="ok", logger=logger)
        never, token = timing.begin_record("r-never")
        timing.end_record(never, token, outcome="stopped", logger=logger)

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(lines) == 1
    assert "record r-admitted (ok)" in lines[0]


def test_timing_logs_can_be_switched_off(monkeypatch, logger, caplog) -> None:
    monkeypatch.setenv("INDEXING_TIMING_LOGS", "false")
    monkeypatch.setenv("INDEXING_SLOW_STEP_SECONDS", "1")
    timeline, token = timing.begin_record("r1")
    timeline.mark(timing.ADMITTED)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.record_stage("embed.dense", 5.0, logger=logger)
        timing.end_record(timeline, token, outcome="ok", logger=logger)

    assert caplog.records == []


@pytest.mark.parametrize("raw", ["", "abc", "0", "-3"])
def test_invalid_threshold_falls_back_to_the_default(monkeypatch, raw) -> None:
    monkeypatch.setenv("INDEXING_SLOW_STEP_SECONDS", raw)

    assert timing.slow_step_seconds() == 30.0

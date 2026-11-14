"""Per-record indexing stage timings (app.utils.indexing_timing)."""

import asyncio
import gc
import json
import logging
import weakref
import zipfile

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError, VectorStoreError
from app.utils import error_class
from app.utils import indexing_timing as timing
from app.utils.logger import JsonFormatter, TextFormatter

NAME = "Quarterly-Secret-Plan.docx"  # a user's file name: never on an index.end line


def _end_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == timing.INDEX_END]


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


def test_end_line_ranks_stages_by_time_and_drops_the_tiny_ones(logger, caplog) -> None:
    timeline, token = timing.begin_record("rec-9", "reindexRecord", "light", started=0.0)
    timeline.mark(timing.ADMITTED, at=0.5)
    timeline.add("graph.read", 0.2)
    timeline.add("lease.index", 0.01)
    for seconds in (10.0, 30.0, 20.0):
        timeline.add("embed.dense", seconds)
    timeline.add("blob.write", 1.0)
    timeline.mark(timing.RELEASED, at=40.0)

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.end_record(timeline, token, outcome="ok", logger=logger)

    (record,) = _end_lines(caplog)
    assert (record.levelno, record.getMessage(), record.event_name) == (logging.INFO, "index.end", "index.end")
    assert (record.outcome, record.disposition, record.record_id, record.event_type, record.tier) == (
        "success", "ack", "rec-9", "reindexRecord", "light")
    assert (record.wait_slot_ms, record.held_ms) == (500, 39500)
    assert isinstance(record.duration_ms, int)
    assert record.stages == "embed.dense=60.0s/3(max 30.0s) blob.write=1.0s graph.read=0.2s"
    assert not hasattr(record, "slow_steps") and not hasattr(record, "reason")


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
    timeline.mark(timing.ADMITTED, at=timeline.started)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        for _ in range(6):
            timing.record_stage("embed.dense", 7.0, detail="batch of 50 texts", logger=logger)
        timing.record_stage("embed.dense", 1.0, logger=logger)
        timing.end_record(timeline, token, outcome="ok", logger=logger)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3
    first = warnings[0]
    assert (first.getMessage(), first.step, first.duration_ms, first.threshold_ms, first.record_id, first.detail) == (
        "index.slow_step", "embed.dense", 7000, 5000, "rec-slow", "batch of 50 texts")
    assert timeline.stage_totals()["embed.dense"][0] == 7
    (end,) = _end_lines(caplog)
    assert end.slow_steps == "embed.dense:6"


def test_slow_step_outside_a_record_still_warns(monkeypatch, logger, caplog) -> None:
    monkeypatch.setenv("INDEXING_SLOW_STEP_SECONDS", "1")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        timing.record_stage("graph.enrich", 2.0, logger=logger)

    (record,) = caplog.records
    assert (record.getMessage(), record.step, record.duration_ms) == ("index.slow_step", "graph.enrich", 2000)
    assert not hasattr(record, "record_id")


def test_one_end_line_per_delivery_admitted_or_not(logger, caplog) -> None:
    admitted, token = timing.begin_record("r-admitted")
    admitted.mark(timing.ADMITTED)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.end_record(admitted, token, outcome="ok", logger=logger)
        never, token = timing.begin_record("r-never", started=0.0)
        timing.end_record(never, token, outcome="requeued", logger=logger)

    ok, requeued = _end_lines(caplog)
    assert (ok.record_id, ok.outcome, ok.levelno) == ("r-admitted", "success", logging.INFO)
    assert (requeued.record_id, requeued.outcome, requeued.disposition, requeued.reason, requeued.levelno) == (
        "r-never", "skipped", "retry", "requeued", logging.WARNING)
    assert isinstance(requeued.wait_slot_ms, int) and not hasattr(requeued, "held_ms")


def test_the_switch_silences_only_the_slow_step_warnings(monkeypatch, logger, caplog) -> None:
    monkeypatch.setenv("INDEXING_TIMING_LOGS", "false")
    monkeypatch.setenv("INDEXING_SLOW_STEP_SECONDS", "1")
    timeline, token = timing.begin_record("r1", started=0.0)
    timeline.mark(timing.ADMITTED, at=1.0)
    timeline.mark(timing.RELEASED, at=9.0)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.record_stage("embed.dense", 5.0, logger=logger)
        timing.end_record(timeline, token, outcome="ok", logger=logger)

    (record,) = caplog.records
    assert record.getMessage() == "index.end"
    assert (record.wait_slot_ms, record.held_ms, record.stages, record.slow_steps) == (
        1000, 8000, "embed.dense=5.0s", "embed.dense:1")


@pytest.mark.parametrize("raw", ["", "abc", "0", "-3"])
def test_invalid_threshold_falls_back_to_the_default(monkeypatch, raw) -> None:
    monkeypatch.setenv("INDEXING_SLOW_STEP_SECONDS", raw)

    assert timing.slow_step_seconds() == 30.0


class ConversionError(Exception):  # stand-ins for docling's own types: only the chain's shape matters
    pass


class DocumentLoadError(Exception):
    pass


def _raise_chain(*links: BaseException) -> BaseException:
    """Raise ``links[-1]``, then each earlier link ``from`` the one after it; return the outermost."""
    def build(i: int) -> None:
        if i == len(links) - 1:
            raise links[i]
        try:
            build(i + 1)
        except BaseException as e:
            raise links[i] from e
    try:
        build(0)
    except BaseException as e:
        return e
    raise AssertionError("unreachable")


def _corrupt() -> BaseException:
    return _raise_chain(
        DocumentProcessingError(f"Failed to process document: Conversion failed for: {NAME}"),
        ConversionError(f"Conversion failed for: {NAME}"),
        DocumentLoadError(f"Input document {NAME} is not valid"),
        zipfile.BadZipFile("File is not a zip file"),
    )


def _zip_as_docx() -> BaseException:
    return _raise_chain(
        DocumentProcessingError(f"Failed to process document: Conversion failed for: {NAME}"),
        ConversionError(f"Conversion failed for: {NAME}"),
        DocumentLoadError(f"Input document {NAME} is not valid"),
        KeyError(f"There is no item named '[Content_Types].xml' in the archive {NAME}"),
    )


def _our_bug() -> BaseException:
    return _raise_chain(DocumentProcessingError(f"Failed to process document {NAME}"), KeyError(NAME))


def _outage() -> BaseException:
    return _raise_chain(VectorStoreError("Failed to store points"), ConnectionError("qdrant refused"))


def _plain(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as e:
        return e


def _deliver(logger, caplog, outcome: str, *notes: dict[str, object]) -> logging.LogRecord:
    timeline, token = timing.begin_record("r1", "newRecord", "heavy", started=0.0)
    timeline.mark(timing.ADMITTED, at=0.0)
    for facts in notes:
        assert timing.note(**facts)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.end_record(timeline, token, outcome=outcome, logger=logger)
    (record,) = _end_lines(caplog)
    return record


def _rendered(record: logging.LogRecord) -> str:
    return TextFormatter().format(record) + JsonFormatter().format(record)


W, E, INFO = logging.WARNING, logging.ERROR, logging.INFO


@pytest.mark.parametrize(("internal", "notes", "want"), [
    pytest.param("ok", [], ("success", "ack", None, INFO, None), id="success"),
    pytest.param("ok", [{"outcome": "skipped", "reason": "duplicate", "duplicate_of": "r0"}],
                 ("skipped", "ack", "duplicate", INFO, None), id="duplicate"),
    pytest.param("requeued", [], ("skipped", "retry", "requeued", W, None), id="requeued"),
    pytest.param("contended", [], ("skipped", "retry", "contended", INFO, None), id="contended"),
    pytest.param("failed", [{"exc": _corrupt(), "reason": "terminal"}, {"disposition": "dead_letter", "attempt": 2}],
                 ("failure", "dead_letter", "terminal", W, "bad_request"), id="corrupt-terminal"),
    pytest.param("failed", [{"exc": _zip_as_docx(), "reason": "terminal"}, {"disposition": "dead_letter"}],
                 ("failure", "dead_letter", "terminal", W, "bad_request"), id="zip-as-docx"),
    pytest.param("failed", [{"exc": _our_bug(), "reason": "terminal"}, {"disposition": "dead_letter"}],
                 ("failure", "dead_letter", "terminal", E, "internal"), id="direct-keyerror"),
    pytest.param("failed", [{"exc": _outage()}, {"disposition": "retry", "attempt": 1}],
                 ("failure", "retry", "exception", W, "unavailable"), id="unavailable-retry"),
    pytest.param("failed", [{"exc": _outage(), "reason": "attempts_exhausted"}, {"disposition": "dead_letter"}],
                 ("failure", "dead_letter", "attempts_exhausted", E, "unavailable"), id="exhausted"),
    pytest.param("requeued", [{"reason": "backstop"}, {"disposition": "dead_letter"}],
                 ("failure", "dead_letter", "backstop", E, None), id="backstop"),
    pytest.param("failed", [{"reason": "unfinished_attempts"}, {"disposition": "dead_letter"}],
                 ("failure", "dead_letter", "unfinished_attempts", E, None), id="unfinished-attempts"),
    pytest.param("failed", [{"outcome": "skipped", "reason": "duplicate"}, {"exc": _outage()}],
                 ("failure", "retry", "duplicate", W, "unavailable"), id="skipped-then-raised"),
    pytest.param("failed", [], ("failure", "retry", "exception", W, None), id="failure-without-class"),
    pytest.param("ok", [{"disposition": "retry", "reason": "ack_failed", "exc": _plain(RuntimeError("xack"))}],
                 ("success", "retry", "ack_failed", W, "internal"), id="ack-failed-after-success"),
    pytest.param("failed", [{"reason": "cancelled", "exc": _plain(asyncio.CancelledError())}],
                 ("failure", "retry", "cancelled", INFO, "cancelled"), id="cancelled"),
    pytest.param("stopped", [], ("failure", "retry", "shutdown", INFO, None), id="shutdown"),
    pytest.param("failed", [{"exc": _plain(TimeoutError()), "timeout_s": 600}],
                 ("failure", "retry", "exception", W, "timeout"), id="timeout"),
])
def test_outcome_and_level_policy(logger, caplog, internal, notes, want) -> None:
    record = _deliver(logger, caplog, internal, *notes)

    outcome, disposition, reason, level, cls = want
    assert (record.outcome, record.disposition, getattr(record, "reason", None), record.levelno) == (
        outcome, disposition, reason, level)
    assert getattr(record, "error_class", None) == cls
    assert NAME not in _rendered(record)
    assert "[REDACTED]" not in _rendered(record)


def test_a_content_failure_has_no_text_and_names_its_innermost_cause(logger, caplog) -> None:
    record = _deliver(logger, caplog, "failed", {"exc": _zip_as_docx(), "reason": "terminal"},
                      {"disposition": "dead_letter", "attempt": 3})
    assert (record.error_type, record.cause_type, record.attempt) == ("DocumentProcessingError", "KeyError", 3)
    assert not hasattr(record, "error_message") and record.exc_info is None and getattr(record, "exc_text", None) is None


def test_our_own_parser_bug_is_one_masked_stack(logger, caplog) -> None:
    record = _deliver(logger, caplog, "failed", {"exc": _our_bug(), "reason": "terminal"}, {"disposition": "dead_letter"})
    assert record.cause_type == "KeyError" and not hasattr(record, "error_message")
    assert "KeyError: <message omitted>" in record.exc_text and NAME not in record.exc_text


def test_an_outage_keeps_its_redacted_text_and_exhaustion_prints_the_stack_once(logger, caplog) -> None:
    record = _deliver(logger, caplog, "failed", {"exc": _outage(), "reason": "attempts_exhausted"},
                      {"disposition": "dead_letter"})
    assert "qdrant refused" in record.error_message and "ConnectionError" in record.exc_text


def test_cause_type_only_when_there_is_a_chain(logger, caplog) -> None:
    record = _deliver(logger, caplog, "failed", {"exc": _plain(TimeoutError()), "timeout_s": 600})
    assert (record.error_type, record.timeout_s) == ("TimeoutError", 600)
    assert not hasattr(record, "cause_type")


def test_an_exception_already_logged_adds_no_second_stack(logger, caplog) -> None:
    exc = _our_bug()
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        error_class.log_failure(logger, "elsewhere.failed", exc, level=logging.ERROR)
    record = _deliver(logger, caplog, "failed", {"exc": exc, "reason": "terminal"}, {"disposition": "dead_letter"})
    assert (record.levelno, record.error_class, record.error_type) == (logging.ERROR, "internal", "DocumentProcessingError")
    assert getattr(record, "exc_text", None) is None and not hasattr(record, "error_message")


def test_noted_ids_win_over_the_timelines(logger, caplog) -> None:
    timeline, token = timing.begin_record(None, "stageJob", "light")
    timing.note(record_id="rec-from-job", stage="classify", job="job-1")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.end_record(timeline, token, outcome="ok", logger=logger)
    (record,) = _end_lines(caplog)
    assert (record.record_id, record.stage, record.job, record.event_type) == ("rec-from-job", "classify", "job-1", "stageJob")


@pytest.mark.asyncio
async def test_note_reaches_the_line_from_a_task_and_is_a_no_op_outside_or_after(logger, caplog) -> None:
    assert timing.note(chunks=1) is False  # outside a record

    timeline, token = timing.begin_record("r1")

    async def sink() -> None:
        timing.note(chunks=42, blocks=7)

    await asyncio.create_task(sink())
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.end_record(timeline, token, outcome="ok", logger=logger)
    (record,) = _end_lines(caplog)
    assert (record.chunks, record.blocks) == (42, 7)
    assert timing.note(chunks=1) is False and timeline.note(chunks=1) is False
    assert timeline.facts == {}


class _Probe(ConnectionError):
    """Built-in exceptions cannot be weak-referenced; a subclass can. A ConnectionError, so the line is a WARNING:
    a record at ERROR keeps the exception in exc_info, and pytest's own report handler keeps the record."""


def test_the_end_line_lets_go_of_the_noted_exception(logger, caplog) -> None:
    timeline, token = timing.begin_record("r1")
    try:
        raise _Probe("held")
    except _Probe as e:
        timing.note(exc=e)
        ref = weakref.ref(e)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        timing.end_record(timeline, token, outcome="failed", logger=logger)
    caplog.clear()
    gc.collect()
    assert ref() is None


def test_the_line_is_one_json_object_with_its_fields(logger, caplog) -> None:
    record = _deliver(logger, caplog, "failed", {"exc": _outage()}, {"disposition": "retry", "attempt": 1})
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "index.end"
    assert {k: data[k] for k in ("outcome", "disposition", "attempt", "error_class", "cause_type", "record_id")} == {
        "outcome": "failure", "disposition": "retry", "attempt": 1, "error_class": "unavailable",
        "cause_type": "ConnectionError", "record_id": "r1"}


def test_end_record_never_raises(logger, caplog, monkeypatch) -> None:
    timeline, token = timing.begin_record("r1")
    monkeypatch.setattr(timing, "log_unit_end", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    timing.end_record(timeline, token, outcome="ok", logger=logger)
    assert timeline.ended and timing.current_timeline() is None


def test_a_source_refusal_is_a_warning_and_a_deleted_record_info(logger, caplog) -> None:
    from app.exceptions.indexing_exceptions import RecordDeletedError
    from app.utils.api_call import ApiCallError

    refusal = ApiCallError("refused", status_code=400)
    refusal.source_reason = "too large"
    record = _deliver(logger, caplog, "failed", {"exc": _plain(refusal), "reason": "terminal"}, {"disposition": "dead_letter"})
    assert (record.levelno, record.reason, record.error_class) == (logging.WARNING, "source_refused", "bad_request")
    caplog.clear()
    record = _deliver(logger, caplog, "failed", {"exc": _plain(RecordDeletedError("r1")), "reason": "terminal"},
                      {"disposition": "dead_letter"})
    assert record.levelno == logging.INFO

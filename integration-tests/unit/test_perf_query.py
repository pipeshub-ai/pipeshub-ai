"""Unit tests for the query benchmark's load driver, metrics and comparison.

The benchmark itself needs a live stack; these pin the parts whose mistakes
would quietly produce wrong numbers: a percentile over the wrong samples, a
failed request counted as a success, a chat turn that never sees its terminal
frame, and a comparison that judges two runs it should have refused.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import bench_query  # noqa: E402
import compare  # noqa: E402
from bench_query import (  # noqa: E402
    CHAT_QUESTIONS,
    OPERATION_MIX,
    SEARCH_QUERIES,
    Recorder,
    Sample,
    drive_user,
    operation_metrics,
    question_set_id,
    run_chat,
    run_search,
)

pytestmark = pytest.mark.unit


class _FakeResponse:
    """Just enough of requests.Response for the SSE parser and the search path."""

    def __init__(self, status_code: int = 200, body: Any = None, chunks: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self._chunks = chunks or []
        self.closed = False

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no JSON")
        return self._body

    def iter_content(self, chunk_size: int = 512) -> Any:
        for chunk in self._chunks:
            if isinstance(chunk, _Delay):
                time.sleep(_SLOW_SECONDS)
                continue
            yield chunk

    def close(self) -> None:
        self.closed = True


class _FakeSearchClient:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self.response = response or _FakeResponse(200, {"records": [{"id": "r1"}]})
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def search(self, query: str, **kwargs: Any) -> Any:
        self.calls.append({"query": query, **kwargs})
        if self.raises:
            raise self.raises
        return self.response


def _frame(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


_SLOW_SECONDS = 0.05


class _Delay:
    """A chunk that takes time to arrive, so a frame can be measurably later."""

    def __bytes__(self) -> bytes:  # pragma: no cover - not used
        return b""


_SLOW = _Delay()


class _FakeConversationsClient:
    def __init__(self, chunks: list[bytes] | None = None, status_code: int = 200) -> None:
        self.chunks = chunks if chunks is not None else [
            _frame("CUSTOM", {"type": "CUSTOM", "name": "conversation_created",
                              "value": {"conversationId": "c1"}}),
            _frame("RUN_STARTED", {"type": "RUN_STARTED"}),
            _frame("STATE_DELTA", {"type": "STATE_DELTA", "delta": [
                {"op": "replace", "path": "/normalizedAnswer", "value": "Revenue grew."}]}),
            _frame("RUN_FINISHED", {"type": "RUN_FINISHED", "result": {"recordsUsed": [{"id": "r1"}]}}),
        ]
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def stream_conversation(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return _FakeResponse(self.status_code, chunks=self.chunks)


# --------------------------------------------------------------------------
# The question set
# --------------------------------------------------------------------------


def test_question_set_id_is_stable_and_moves_with_the_questions(monkeypatch) -> None:
    first = question_set_id()
    assert first == question_set_id()
    assert len(first) == 12

    monkeypatch.setattr(bench_query, "SEARCH_QUERIES", (*SEARCH_QUERIES, "an extra question"))
    assert question_set_id() != first


def test_every_question_is_drawn_from_the_corpus_vocabulary() -> None:
    """A query no document can answer measures the empty-result path instead."""
    from corpus import WORDS

    vocabulary = {w.lower() for w in WORDS}
    for query in SEARCH_QUERIES:
        words = query.lower().split()
        assert any(w in vocabulary for w in words), f"no word of {query!r} is in the corpus"


# --------------------------------------------------------------------------
# One search
# --------------------------------------------------------------------------


def test_a_search_records_its_operation_and_whether_it_found_anything() -> None:
    client = _FakeSearchClient()
    plain = run_search(client, "quarterly revenue report", None)
    filtered = run_search(client, "quarterly revenue report", "kb-1")

    assert (plain.operation, plain.ok, plain.with_sources) == ("search", True, True)
    assert filtered.operation == "search_filtered"
    assert client.calls[0].get("filters") is None
    assert client.calls[1]["filters"] == {"kb": ["kb-1"]}
    assert client.calls[1]["limit"] == bench_query.SEARCH_LIMIT


def test_a_refused_search_is_an_error_not_a_fast_success() -> None:
    sample = run_search(_FakeSearchClient(_FakeResponse(429)), "budget forecast", None)
    assert (sample.ok, sample.error) == (False, "HTTP 429")


def test_a_search_that_raises_is_recorded_with_its_reason() -> None:
    sample = run_search(_FakeSearchClient(raises=TimeoutError("read timed out")), "budget forecast", None)
    assert sample.ok is False
    assert "TimeoutError" in sample.error


def test_an_empty_result_is_a_success_with_no_sources() -> None:
    sample = run_search(_FakeSearchClient(_FakeResponse(200, {"records": []})), "budget forecast", None)
    assert (sample.ok, sample.with_sources) == (True, False)


# --------------------------------------------------------------------------
# One chat turn
# --------------------------------------------------------------------------


def test_a_chat_turn_is_timed_to_its_terminal_frame() -> None:
    sample = run_chat(_FakeConversationsClient(), CHAT_QUESTIONS[0], timeout=5)
    assert (sample.operation, sample.ok, sample.with_sources) == ("chat", True, True)
    assert sample.first_answer_seconds is not None
    assert sample.first_answer_seconds <= sample.seconds


def test_the_first_answer_time_ignores_the_conversation_created_frame() -> None:
    """Node flushes that frame as soon as the conversation row exists, before
    the query service has retrieved anything, so timing it would measure Mongo."""
    chunks = [
        _frame("CUSTOM", {"type": "CUSTOM", "name": "conversation_created",
                          "value": {"conversationId": "c1"}}),
        _frame("RUN_STARTED", {"type": "RUN_STARTED"}),
        _SLOW,
        _frame("STATE_DELTA", {"type": "STATE_DELTA",
                               "delta": [{"op": "replace", "path": "/normalizedAnswer", "value": "Revenue grew."}]}),
        _frame("RUN_FINISHED", {"type": "RUN_FINISHED", "result": {"recordsUsed": [{"id": "r1"}]}}),
    ]
    sample = run_chat(_FakeConversationsClient(chunks), CHAT_QUESTIONS[0], timeout=5)
    assert sample.ok is True
    assert sample.first_answer_seconds is not None
    # The delay sits between the created frame and the answer, so a metric that
    # timed the first frame of the stream would be under it.
    assert sample.first_answer_seconds >= _SLOW_SECONDS


def test_text_message_content_also_counts_as_the_first_answer() -> None:
    chunks = [
        _frame("CUSTOM", {"type": "CUSTOM", "name": "conversation_created", "value": {}}),
        _SLOW,
        _frame("TEXT_MESSAGE_CONTENT", {"type": "TEXT_MESSAGE_CONTENT", "delta": "Revenue"}),
        _frame("RUN_FINISHED", {"type": "RUN_FINISHED", "result": {"recordsUsed": []}}),
    ]
    sample = run_chat(_FakeConversationsClient(chunks), CHAT_QUESTIONS[0], timeout=5)
    assert sample.first_answer_seconds is not None
    assert sample.first_answer_seconds >= _SLOW_SECONDS


def test_a_turn_that_never_answers_leaves_the_first_answer_time_unset() -> None:
    chunks = [
        _frame("CUSTOM", {"type": "CUSTOM", "name": "conversation_created", "value": {}}),
        _frame("RUN_FINISHED", {"type": "RUN_FINISHED", "result": {"recordsUsed": []}}),
    ]
    sample = run_chat(_FakeConversationsClient(chunks), CHAT_QUESTIONS[0], timeout=5)
    assert sample.ok is True
    assert sample.first_answer_seconds is None


def test_a_child_frame_does_not_end_the_turn() -> None:
    """A sub-agent's own RUN_FINISHED arrives first and carries no answer."""
    chunks = [
        _frame("RUN_FINISHED", {"type": "RUN_FINISHED", "parentRunId": "root-1"}),
        _frame("RUN_FINISHED", {"type": "RUN_FINISHED", "result": {"recordsUsed": []}}),
    ]
    sample = run_chat(_FakeConversationsClient(chunks), CHAT_QUESTIONS[0], timeout=5)
    assert sample.ok is True
    assert sample.with_sources is False


def test_a_stream_error_is_a_failed_turn() -> None:
    chunks = [_frame("RUN_ERROR", {"type": "RUN_ERROR", "message": "The AI model didn't respond."})]
    sample = run_chat(_FakeConversationsClient(chunks), CHAT_QUESTIONS[0], timeout=5)
    assert sample.ok is False
    assert "didn't respond" in sample.error


def test_a_stream_that_ends_without_an_answer_is_a_failed_turn() -> None:
    chunks = [_frame("RUN_STARTED", {"type": "RUN_STARTED"})]
    sample = run_chat(_FakeConversationsClient(chunks), CHAT_QUESTIONS[0], timeout=5)
    assert (sample.ok, sample.error) == (False, "stream ended with no answer")


def test_a_refused_chat_request_is_a_failed_turn() -> None:
    sample = run_chat(_FakeConversationsClient(status_code=503), CHAT_QUESTIONS[0], timeout=5)
    assert (sample.ok, sample.error) == (False, "HTTP 503")


# --------------------------------------------------------------------------
# The load driver
# --------------------------------------------------------------------------


class _Args:
    think_time = 0.0
    chat_timeout = 5.0


def test_a_user_stops_at_the_deadline() -> None:
    recorder = Recorder()
    clients = {"search": _FakeSearchClient(), "conversations": _FakeConversationsClient()}
    drive_user(0, time.perf_counter() - 1, _Args(), "kb-1", recorder, clients)
    assert recorder.samples == []


def test_a_user_follows_the_mix_and_records_every_turn() -> None:
    recorder = Recorder()
    clients = {"search": _FakeSearchClient(), "conversations": _FakeConversationsClient()}
    drive_user(0, time.perf_counter() + 0.2, _Args(), "kb-1", recorder, clients)

    assert recorder.samples, "the user recorded nothing"
    done = [s.operation for s in recorder.samples]
    assert done == [OPERATION_MIX[i % len(OPERATION_MIX)] for i in range(len(done))]
    assert all(call["filters"] == {"kb": ["kb-1"]} for call in clients["search"].calls if "filters" in call)


def test_users_start_at_different_points_in_the_question_list() -> None:
    recorders = []
    for user in range(2):
        recorder = Recorder()
        clients = {"search": _FakeSearchClient(), "conversations": _FakeConversationsClient()}
        drive_user(user, time.perf_counter() + 0.05, _Args(), "kb-1", recorder, clients)
        recorders.append(clients["search"].calls[0]["query"])
    assert recorders[0] != recorders[1]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_metrics_count_only_successful_requests_in_the_percentiles() -> None:
    samples = [
        Sample("search", 0.1, True),
        Sample("search", 0.3, True),
        Sample("search", 9.9, False, error="HTTP 500"),
    ]
    metrics = operation_metrics(samples, wall=60.0)

    assert metrics["count"] == 3
    assert metrics["succeeded"] == 2
    assert metrics["errors"] == 1
    assert metrics["error_rate"] == pytest.approx(0.3333, abs=1e-4)
    assert metrics["latency_seconds"]["p50"] == pytest.approx(0.2)
    assert metrics["latency_seconds"]["max"] == pytest.approx(0.3)
    assert metrics["per_minute"] == pytest.approx(3.0)


def test_first_answer_timings_appear_only_when_they_were_measured() -> None:
    assert "first_answer_seconds" not in operation_metrics([Sample("search", 0.1, True)], wall=10)
    chat = operation_metrics([Sample("chat", 8.0, True, first_answer_seconds=1.5)], wall=10)
    assert chat["first_answer_seconds"]["p50"] == pytest.approx(1.5)


def test_no_samples_leaves_the_numbers_empty_rather_than_zero() -> None:
    metrics = operation_metrics([], wall=60.0)
    assert metrics["count"] == 0
    assert metrics["error_rate"] is None
    assert metrics["latency_seconds"]["p95"] is None


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _query_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "benchmark": "query",
        "environment": {
            "label": "ci-query-neo4j-4cpu",
            "graph_db": "neo4j",
            "message_broker": "redis",
            "ai_models": {"llm": "openai/gpt-4o-mini", "embedding": "openai/text-embedding-3-small"},
        },
        "corpus": {"docs": 120, "seed": 1337, "kinds": ["txt", "md"]},
        "profile": {
            "users": 4,
            "duration_seconds": 300,
            "think_time_seconds": 1.0,
            "question_set": "abc123def456",
            "mix": list(OPERATION_MIX),
        },
        "metrics": {
            "operations_per_minute": 40.0,
            "docs_indexed": 120,
            "seeding_stopped_early": None,
            "operations": {
                "search": {"errors": 0, "latency_seconds": {"p50": 0.2, "p95": 0.5},
                           "with_sources_rate": 1.0},
                "search_filtered": {"errors": 0, "latency_seconds": {"p50": 0.2, "p95": 0.5},
                                    "with_sources_rate": 1.0},
                "chat": {
                    "errors": 0,
                    "latency_seconds": {"p50": 8.0, "p95": 12.0},
                    "first_answer_seconds": {"p50": 1.0, "p95": 2.0},
                    "with_sources_rate": 0.9,
                },
            },
        },
    }
    for key, value in overrides.items():
        result[key] = value
    return result


def test_a_slower_search_is_flagged_and_a_steady_one_is_not() -> None:
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["operations"]["search"]["latency_seconds"]["p95"] = 0.9  # +80%

    rows, mismatches = compare.compare(baseline, current)
    assert mismatches == []
    flagged = {row.name for row in rows if row.regressed}
    assert flagged == {"Search p95"}


def test_a_drop_in_throughput_is_flagged() -> None:
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["operations_per_minute"] = 28.0  # -30%
    rows, _ = compare.compare(baseline, current)
    assert any(row.regressed and "Throughput" in row.name for row in rows)


def test_a_new_failed_turn_is_flagged() -> None:
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["operations"]["chat"]["errors"] = 1
    rows, _ = compare.compare(baseline, current)
    assert any(row.regressed and row.name == "Failed searches and chat turns" for row in rows)


def test_a_different_question_set_is_not_judged() -> None:
    baseline = _query_result()
    current = _query_result()
    current["profile"]["question_set"] = "999999999999"
    _, mismatches = compare.compare(baseline, current)
    assert any("question set" in m for m in mismatches)


def test_more_users_make_the_runs_incomparable() -> None:
    baseline = _query_result()
    current = _query_result()
    current["profile"]["users"] = 8
    _, mismatches = compare.compare(baseline, current)
    assert any("simulated users" in m for m in mismatches)


def test_an_indexing_baseline_is_never_compared_with_a_query_run() -> None:
    rows, mismatches = compare.compare({"benchmark": "indexing", "metrics": {}}, _query_result())
    assert rows == []
    assert mismatches == ["benchmark: baseline 'indexing', this run 'query'"]
    assert "Nothing was compared." in compare.render(rows, mismatches, "baselines/x.json")


def test_an_operation_missing_on_one_side_is_reported_as_not_measured() -> None:
    baseline = _query_result()
    current = _query_result()
    del current["metrics"]["operations"]["chat"]["first_answer_seconds"]
    rows, _ = compare.compare(baseline, current)
    first_frame = next(row for row in rows if row.name == "Chat first answer frame p95")
    assert first_frame.regressed is False
    assert first_frame.note == "not measured on one side"


def test_indexing_results_still_compare_as_they_did() -> None:
    def indexing(records_per_minute: float) -> dict[str, Any]:
        return {
            "benchmark": "indexing",
            "environment": {
                "label": "ci-neo4j-4cpu", "graph_db": "neo4j", "message_broker": "redis",
                "ai_models": {"llm": "openai/gpt-4o-mini"},
            },
            "corpus": {"docs": 500, "seed": 1337, "kinds": ["txt"]},
            "metrics": {
                "records_per_minute": records_per_minute,
                "time_to_indexed_seconds": {"p50": 10.0, "p95": 20.0},
                "wall_seconds": 600.0,
                "peak_indexing_rss_mb": 900.0,
                "failures": {"total": 0},
            },
        }

    rows, mismatches = compare.compare(indexing(100.0), indexing(70.0))
    assert mismatches == []
    assert any(row.regressed and "Throughput" in row.name for row in rows)


# --------------------------------------------------------------------------
# The result the benchmark writes is the result the comparison reads
# --------------------------------------------------------------------------


class _Sampler:
    peak_container = 1_200_000_000.0


def _built_result() -> dict[str, Any]:
    import argparse
    from datetime import datetime, timezone

    from corpus import generate_corpus
    from stack import RunState

    args = argparse.Namespace(
        label="unit-test", graph_db="neo4j", users=2, duration=60.0,
        think_time=1.0, warmup=1,
    )
    state = RunState()
    state.uploaded_at = {"rec-1": 0.0, "rec-2": 0.0, "rec-3": 0.0}
    state.status = {"rec-1": "COMPLETED", "rec-2": "COMPLETED", "rec-3": "COMPLETED"}
    recorder = Recorder()
    recorder.add(Sample("search", 0.2, True, with_sources=True))
    recorder.add(Sample("search_filtered", 0.3, True, with_sources=True))
    recorder.add(Sample("chat", 9.0, True, first_answer_seconds=1.2, with_sources=True))
    recorder.add(Sample("chat", 9.5, False, error="HTTP 500"))
    return bench_query.build_result(
        args, generate_corpus(3, seed=7), state, recorder, 60.0, _Sampler(),
        {"llm": "openai/gpt-4o-mini", "embedding": "openai/text-embedding-3-small"},
        datetime.now(timezone.utc),
    )


def test_the_written_result_is_json_and_carries_what_the_comparison_reads() -> None:
    result = _built_result()
    json.dumps(result)  # the file is written with json.dumps; a non-serialisable value would fail here

    assert result["benchmark"] == "query"
    assert result["metrics"]["docs_indexed"] == 3
    assert result["metrics"]["docs_uploaded"] == 3
    assert result["metrics"]["errors_by_kind"] == {"HTTP 500": 1}
    assert result["profile"]["question_set"] == question_set_id()

    rows, mismatches = compare.compare(result, result)
    assert mismatches == []
    assert not [row for row in rows if row.regressed]
    assert {row.name for row in rows} >= {"Search p95", "Chat turn p95", "Failed searches and chat turns"}


def test_the_summary_says_what_was_measured() -> None:
    summary = bench_query.render_summary(_built_result())
    assert "Query benchmark — unit-test" in summary
    assert "2 simulated users for 60.0s" in summary
    assert "chat" in summary and "search_filtered" in summary
    assert "HTTP 500 ×1" in summary
    assert "cited a document" in summary


# --------------------------------------------------------------------------
# A run over an empty or half-seeded knowledge base is not judged
# --------------------------------------------------------------------------


def test_a_run_whose_seeding_stopped_early_is_not_judged() -> None:
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["seeding_stopped_early"] = "indexing did not finish within 1800s"
    # Empty results are fast, so this would otherwise read as the best run yet.
    current["metrics"]["operations"]["search"]["latency_seconds"] = {"p50": 0.01, "p95": 0.02}

    _, mismatches = compare.compare(baseline, current)
    assert any("seeding did not finish" in m for m in mismatches)


def test_a_half_seeded_corpus_is_not_judged() -> None:
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["docs_indexed"] = 40  # of 120

    _, mismatches = compare.compare(baseline, current)
    assert any("only 40 of 120 documents were indexed" in m for m in mismatches)


def test_a_short_baseline_is_called_out_too() -> None:
    baseline = _query_result()
    baseline["metrics"]["docs_indexed"] = 10
    _, mismatches = compare.compare(baseline, _query_result())
    assert any(m.startswith("baseline: only 10 of 120") for m in mismatches)


def test_searches_that_stopped_finding_anything_are_flagged() -> None:
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["operations"]["search"]["with_sources_rate"] = 0.4

    rows, mismatches = compare.compare(baseline, current)
    assert mismatches == []
    assert any(row.regressed and row.name == "Searches that found a hit" for row in rows)


def test_answers_that_stopped_citing_documents_are_flagged() -> None:
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["operations"]["chat"]["with_sources_rate"] = 0.1

    rows, _ = compare.compare(baseline, current)
    assert any(row.regressed and row.name == "Answers that cited a document" for row in rows)


def test_a_steady_hit_rate_is_not_flagged() -> None:
    rows, _ = compare.compare(_query_result(), _query_result())
    assert not [row for row in rows if row.regressed]


def test_the_search_hit_rate_reads_the_real_response_shape() -> None:
    """The search routes wrap their hits; reading the top level finds nothing."""
    body = {
        "searchResponse": {
            "searchResults": [{"content": "quarterly revenue grew", "_id": "h1"}],
            "records": [{"_id": "r1", "recordName": "Quarterly report 0001.txt"}],
        },
        "filters": {"applied": {"values": {"page": 1, "limit": 20}}},
    }
    sample = run_search(_FakeSearchClient(_FakeResponse(200, body)), "quarterly revenue report", "kb-1")
    assert (sample.ok, sample.with_sources) == (True, True)
    assert bench_query.search_hits(body)

    empty = {"searchResponse": {"searchResults": [], "records": []}}
    assert bench_query.search_hits(empty) == []
    assert run_search(_FakeSearchClient(_FakeResponse(200, empty)), "q", None).with_sources is False


def test_a_flat_response_still_counts() -> None:
    flat = {"searchResults": [{"content": "hit"}]}
    assert bench_query.search_hits(flat)


# --------------------------------------------------------------------------
# The questions are never asked over a corpus that is not there
# --------------------------------------------------------------------------


def _seeded(indexed: int, total: int, stopped_early: str = "") -> Any:
    from stack import RunState

    state = RunState()
    state.status = {f"rec-{i}": ("COMPLETED" if i < indexed else "FAILED") for i in range(total)}
    state.uploaded_at = {f"rec-{i}": 0.0 for i in range(total)}
    state.stopped_early = stopped_early
    return state


class _Corpus:
    def __init__(self, count: int) -> None:
        self.files = tuple(range(count))


def test_a_fully_indexed_corpus_lets_the_questions_start() -> None:
    bench_query.check_seed_is_usable(_seeded(10, 10), _Corpus(10), 1.0)


def test_a_half_seeded_corpus_stops_the_run_before_the_questions() -> None:
    with pytest.raises(SystemExit) as stop:
        bench_query.check_seed_is_usable(_seeded(4, 10), _Corpus(10), 1.0)
    message = str(stop.value)
    assert "only 4 of 10 documents were indexed" in message
    assert "FAILED 6" in message
    assert "--require-indexed" in message


def test_seeding_that_stopped_early_stops_the_run() -> None:
    with pytest.raises(SystemExit) as stop:
        bench_query.check_seed_is_usable(_seeded(9, 10, "indexing did not finish within 1800s"), _Corpus(10), 1.0)
    assert "seeding did not finish" in str(stop.value)


def test_a_deliberate_tolerance_is_honoured() -> None:
    bench_query.check_seed_is_usable(_seeded(9, 10), _Corpus(10), 0.9)


def test_an_unknown_benchmark_is_refused_rather_than_guessed() -> None:
    """Guessing would read a query result with indexing's checks and raise."""
    odd = {"benchmark": "connector-sync", "metrics": {}}
    rows, mismatches = compare.compare(odd, odd)
    assert rows == []
    assert mismatches == ["benchmark: 'connector-sync' is not one this can compare"]


def test_filtered_searches_that_stopped_finding_anything_are_flagged() -> None:
    """A broken knowledge-base filter returns nothing, which is fast and counts
    as a success, so the unfiltered row alone would not notice."""
    baseline = _query_result()
    current = _query_result()
    current["metrics"]["operations"]["search_filtered"]["with_sources_rate"] = 0.0
    current["metrics"]["operations"]["search_filtered"]["latency_seconds"] = {"p50": 0.05, "p95": 0.08}

    rows, mismatches = compare.compare(baseline, current)
    assert mismatches == []
    flagged = {row.name for row in rows if row.regressed}
    assert "Filtered searches that found a hit" in flagged
    # The unfiltered searches are untouched, which is exactly why this needs its own row.
    assert "Searches that found a hit" not in flagged

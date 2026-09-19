"""Unit tests for the indexing benchmark's corpus, arithmetic and comparison.

The benchmark itself needs a live stack; these pin the parts whose mistakes
would quietly produce wrong numbers: a corpus that changes between runs, a
salt that fails to change it (the indexer then skips work by MD5), a
percentile off by one rank, and a comparison that flags the wrong direction.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import sys
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import compare  # noqa: E402
from bench_indexing import (  # noqa: E402
    RunState,
    _poll_once,
    _upload,
    indexing_rss_bytes,
    parse_docker_mem,
    percentile,
    should_stop_waiting,
)
from corpus import MIMETYPES, _folder_tree, generate_corpus  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def corpus():
    return generate_corpus(60, seed=1)


def test_same_seed_gives_identical_bytes(corpus) -> None:
    again = generate_corpus(60, seed=1)
    assert [f.content for f in corpus.files] == [f.content for f in again.files]
    assert corpus.folders == again.folders


def test_salt_changes_every_file_but_nothing_else(corpus) -> None:
    salted = generate_corpus(60, seed=1, salt="run abc")
    assert all(a.content != b.content for a, b in zip(corpus.files, salted.files))
    assert [(f.name, f.folder, f.kind) for f in corpus.files] == [
        (f.name, f.folder, f.kind) for f in salted.files
    ]


def test_every_file_is_unique_and_every_kind_appears(corpus) -> None:
    assert len({hashlib.md5(f.content).hexdigest() for f in corpus.files}) == len(corpus.files)
    assert len({f.name for f in corpus.files}) == len(corpus.files)
    assert {f.kind for f in corpus.files} == set(MIMETYPES)


def test_kinds_narrows_the_mix_and_rejects_unknown_kinds() -> None:
    text_only = generate_corpus(40, seed=1, kinds=("txt", "md"))
    assert {f.kind for f in text_only.files} <= {"txt", "md"}
    assert text_only.describe()["kinds"] == ["txt", "md"]
    with pytest.raises(ValueError):
        generate_corpus(5, seed=1, kinds=("txt", "pptx"))


def test_names_and_folders_carry_non_ascii(corpus) -> None:
    assert any(not f.name.isascii() for f in corpus.files)
    assert any(not "".join(path).isascii() for path in corpus.folders)
    assert any(len(path) > 1 for path in _folder_tree(random.Random(11), 30))


def test_folders_list_parents_before_children(corpus) -> None:
    seen: set[tuple[str, ...]] = set()
    for path in corpus.folders:
        assert len(path) == 1 or path[:-1] in seen
        seen.add(path)


def test_office_files_open_as_zips_and_pdfs_look_like_pdfs(corpus) -> None:
    for f in corpus.files:
        if f.kind in ("docx", "xlsx"):
            assert zipfile.ZipFile(io.BytesIO(f.content)).testzip() is None
        elif f.kind == "pdf":
            assert f.content.startswith(b"%PDF-1.4") and f.content.rstrip().endswith(b"%%EOF")


def test_percentile_interpolates_like_numpy() -> None:
    values = [float(v) for v in range(1, 11)]
    assert percentile(values, 50) == pytest.approx(5.5)
    assert percentile(values, 95) == pytest.approx(9.55)
    assert percentile([3.0], 99) == 3.0
    assert percentile([], 50) is None


def test_docker_memory_and_indexing_rss_parsing() -> None:
    assert parse_docker_mem("805.9MiB / 10GiB") == pytest.approx(805.9 * 1024**2)
    assert parse_docker_mem("1.2GB / 16GB") == pytest.approx(1.2e9)
    assert parse_docker_mem("--") is None
    ps = (
        "  PID  PPID   RSS COMMAND\n"
        "   10     1  1000 python -m app.indexing_main\n"
        "   11    10   500 python -c from multiprocessing.spawn import spawn_main\n"
        "   12     1  9999 python -m app.query_main\n"
    )
    assert indexing_rss_bytes(ps) == 1500 * 1024
    assert indexing_rss_bytes("  PID  PPID   RSS COMMAND\n") is None


def test_indexing_rss_reads_docker_top_and_ignores_shell_wrappers() -> None:
    # docker top: host PIDs, a COMMAND header, and a shell whose command line
    # names every service. Counting from the shell would sum the whole container.
    top = (
        "PID       PPID      RSS       COMMAND\n"
        "4100      4000      1852      /bin/sh -c python -m app.query_main & python -m app.indexing_main\n"
        "4101      4100      9999      python -m app.query_main\n"
        "4102      4100      2000      /usr/local/bin/python3 -m app.indexing_main\n"
        "4103      4102      300       /usr/local/bin/python3 -c from multiprocessing.spawn import spawn_main\n"
    )
    assert indexing_rss_bytes(top) == 2300 * 1024


def _result(rpm: float, p95: float, failures: int = 0) -> dict:
    return {
        "environment": {"label": "ci", "graph_db": "neo4j", "message_broker": "redis", "ai_models": "m"},
        "corpus": {"docs": 500, "seed": 1337, "kinds": ["txt", "md"]},
        "metrics": {
            "records_per_minute": rpm,
            "wall_seconds": 600.0,
            "time_to_indexed_seconds": {"p50": 10.0, "p95": p95, "p99": p95},
            "peak_indexing_rss_mb": 900.0,
            "failures": {"total": failures},
        },
    }


def _regressed(rows: list) -> set[str]:
    return {r.name for r in rows if r.regressed}


def test_compare_flags_throughput_drop_and_p95_rise_past_thresholds() -> None:
    rows, mismatches = compare.compare(_result(100, 20), _result(79, 26.1, failures=1))
    assert mismatches == []
    assert _regressed(rows) == {
        "Throughput (records/min)",
        "Time to indexed p95",
        "Failed or unfinished records",
    }


def test_compare_accepts_movement_inside_thresholds_and_improvements() -> None:
    rows, _ = compare.compare(_result(100, 20), _result(81, 25.9))
    assert _regressed(rows) == set()
    rows, _ = compare.compare(_result(100, 20), _result(300, 5))
    assert _regressed(rows) == set()


def test_compare_names_what_makes_runs_incomparable() -> None:
    other = _result(100, 20)
    other["corpus"]["docs"] = 50
    other["environment"]["label"] = "laptop"
    _, mismatches = compare.compare(_result(100, 20), other)
    assert len(mismatches) == 2
    assert any(m.startswith("docs:") for m in mismatches)


def test_placeholder_baseline_reports_without_judging(tmp_path, monkeypatch, capsys) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"placeholder": true, "note": "No CI run yet."}', encoding="utf-8")
    current = tmp_path / "current.json"
    current.write_text(json.dumps(_result(1, 999, failures=50)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "compare.py", "--baseline", str(baseline), "--current", str(current), "--fail-on-regression",
    ])
    assert compare.main() == 0
    assert "No CI run yet." in capsys.readouterr().out


class _Listing:
    """A stand-in for KBClient whose record listing the test controls."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def list_records(self, kb_id: str, page: int = 1, limit: int = 200) -> dict:
        return {"items": self.items, "pagination": {"totalPages": 1}}


def test_poll_times_only_uploaded_records_and_never_before_their_upload() -> None:
    state = RunState()
    listing = _Listing([{"id": "early", "indexingStatus": "COMPLETED"}])
    # Indexed before its upload call returned: known status, but not timed yet.
    _poll_once(listing, "kb", state)
    assert state.status["early"] == "COMPLETED"
    assert "early" not in state.finished_at

    state.uploaded_at["early"] = time.perf_counter()
    _poll_once(listing, "kb", state)
    first = state.finished_at["early"]
    assert first >= state.uploaded_at["early"]

    _poll_once(listing, "kb", state)
    assert state.finished_at["early"] == first


def test_upload_times_a_record_a_poll_already_saw_finish() -> None:
    state = RunState()
    _poll_once(_Listing([{"id": "early", "indexingStatus": "COMPLETED"}]), "kb", state)
    kb = MagicMock()
    kb.upload_file.return_value = {"records": [{"recordId": "early"}, {"recordId": "later"}]}
    f = generate_corpus(1, seed=1).files[0]

    _upload(kb, "kb", None, f, state)

    assert state.finished_at["early"] == state.uploaded_at["early"]
    assert "later" not in state.finished_at


def test_poll_leaves_in_flight_records_untimed() -> None:
    state = RunState()
    state.uploaded_at["r1"] = time.perf_counter()
    _poll_once(_Listing([{"id": "r1", "indexingStatus": "IN_PROGRESS"}]), "kb", state)
    assert state.status["r1"] == "IN_PROGRESS"
    assert state.finished_at == {}


def test_stops_early_only_when_everything_left_was_never_listed() -> None:
    state = RunState()
    state.uploaded_at.update({"gone": 0.0, "slow": 0.0})
    state.status["slow"] = "IN_PROGRESS"
    # Something listed is still indexing: keep waiting.
    assert should_stop_waiting(state, uploads_done=True, now=1000.0, grace=300) == ""

    state.finished_at["slow"] = 50.0
    assert should_stop_waiting(state, uploads_done=True, now=100.0, grace=300) == ""
    assert "never appeared" in should_stop_waiting(state, uploads_done=True, now=301.0, grace=300)
    assert should_stop_waiting(state, uploads_done=False, now=301.0, grace=300) == ""

    state.finished_at["gone"] = 400.0
    assert should_stop_waiting(state, uploads_done=True, now=401.0, grace=300) == "done"


def test_list_records_reads_the_flattened_knowledge_hub_listing() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from helper.clients.kb_client import KBClient

    http = MagicMock()
    http.request.return_value.json.return_value = {"items": [], "pagination": {"totalPages": 0}}
    KBClient(http).list_records("kb-1", page=2, limit=50)

    method, path = http.request.call_args.args
    params = http.request.call_args.kwargs["params"]
    assert (method, path) == ("GET", "/api/v1/knowledgeBase/knowledge-hub/nodes/app/kb-1")
    assert params == {"flattened": "true", "nodeTypes": "record", "page": 2, "limit": 50}

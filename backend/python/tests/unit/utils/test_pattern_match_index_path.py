"""The grep layer answers from the lexical index, and scans files only for
what the index cannot express, saying so."""

import asyncio
import json
import logging
import os
import random
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.pattern_match import (
    PatternMatchCoverage,
    render_pattern_match_coverage_note,
    run_pattern_match_with_llm_grep,
)
from tests.support.lexical_index import InvertedIndexSearcher


@pytest.fixture(autouse=True)
def _no_cached_grep_generation() -> Iterator[None]:
    from app.utils import pattern_match

    pattern_match._grep_commands_cache.clear()
    pattern_match._grep_llm_cache.clear()
    yield
    pattern_match._grep_commands_cache.clear()
    pattern_match._grep_llm_cache.clear()


_ORG = "org-1"
_CONNECTOR = "wiki"
_NEEDLE_TEXT = "rollback plan for ERR_GATEWAY_7731 in the edge proxy"


def _kwargs(**overrides) -> dict:
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={"storageType": "local", "mountName": "PipesHub"})
    graph = MagicMock()
    graph.get_org_apps = AsyncMock(return_value=[{"_key": _CONNECTOR}])
    graph.get_accessible_containers = AsyncMock(side_effect=RuntimeError("not needed"))

    async def _readable(vrids, *_args: object, **_kw: object) -> dict[str, str]:
        return {v: f"rec-{v}" for v in vrids}

    graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=_readable)
    graph.get_records_by_record_ids = AsyncMock(
        side_effect=lambda record_ids, org_id: [{"_key": r, "recordName": r} for r in record_ids],
    )
    return {
        "query": "ERR_GATEWAY_7731 rollback", "config_service": config_service, "org_id": _ORG,
        "user_id": "u1", "graph_provider": graph, "filters": None,
        "logger_instance": logging.getLogger("test"), "llm": None, **overrides,
    }


_STORE_RECORDS = 4000
# What the scan costs per stored file, as a large store costs it: a 100k-record
# store took tens of seconds. Set here so the outcome does not ride on this
# machine's disk cache.
_SCAN_SECONDS_PER_FILE = 0.001
_BUDGET_SECONDS = 1.0


@pytest.fixture
def large_store(tmp_path) -> tuple:
    """A blob store of record files with the needle written last, and its chunks."""
    rng = random.Random(3)
    words = [f"w{i}" for i in range(3000)]
    connector_dir = tmp_path / _ORG / "PipesHub" / "records" / _CONNECTOR
    chunks: list[tuple[str, str]] = []
    for i in range(_STORE_RECORDS + 1):
        vrid = f"{i:08x}-0000-0000-0000-000000000000"
        text = _NEEDLE_TEXT if i == _STORE_RECORDS else " ".join(rng.choices(words, k=300))
        d = connector_dir / f"g{i % 40}" / f"d{i}"
        d.mkdir(parents=True)
        (d / f"record_{vrid}.json").write_text(json.dumps({"record": {"block_containers": {"blocks": [{"data": text}]}}}))
        chunks.append((vrid, text))
    return tmp_path, chunks, chunks[-1][0]


def _scan_that_costs_the_store() -> AbstractContextManager[object]:
    from app.agents.actions.storage_search import storage_search

    real = storage_search._run_subprocess

    async def _run(command: str, *, cwd: str, **kwargs: object) -> tuple[bool, str]:
        files = sum(len(names) for _root, _dirs, names in os.walk(cwd))
        await asyncio.sleep(files * _SCAN_SECONDS_PER_FILE)
        return await real(command, cwd=cwd, **kwargs)

    return patch.object(storage_search, "_run_subprocess", _run)


class TestLargeStore:
    @pytest.mark.asyncio
    async def test_a_hit_the_scan_cannot_reach_in_its_budget_is_found_through_the_index(self, large_store) -> None:
        mount, chunks, needle_vrid = large_store
        assert _STORE_RECORDS * _SCAN_SECONDS_PER_FILE > 2 * _BUDGET_SECONDS

        with patch("app.agents.actions.storage_search.storage_search._resolve_mount_root", return_value=str(mount)), \
                patch("app.utils.pattern_match._PATTERN_MATCH_TOTAL_BUDGET", _BUDGET_SECONDS), \
                _scan_that_costs_the_store():
            scan_coverage = PatternMatchCoverage()
            scanned = await run_pattern_match_with_llm_grep(**_kwargs(), coverage=scan_coverage)
            index_coverage = PatternMatchCoverage()
            searcher = InvertedIndexSearcher(chunks)
            started = time.perf_counter()
            looked_up = await run_pattern_match_with_llm_grep(
                **_kwargs(), coverage=index_coverage, retrieval_service=searcher,
            )
            index_seconds = time.perf_counter() - started

        assert needle_vrid not in {r["virtual_record_id"] for r in scanned}
        assert "time limit" in render_pattern_match_coverage_note(scan_coverage)
        assert [r["virtual_record_id"] for r in looked_up] == [needle_vrid]
        assert index_seconds < _BUDGET_SECONDS
        assert index_coverage.complete
        assert "incomplete" not in render_pattern_match_coverage_note(index_coverage)


def _llm_commands(*commands: str) -> AbstractContextManager[AsyncMock]:
    return patch(
        "app.utils.pattern_match.generate_grep_command_via_llm",
        new_callable=AsyncMock, return_value=list(commands),
    )


class TestIndexOrScan:
    @pytest.mark.asyncio
    async def test_a_command_the_index_can_express_is_never_scanned(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "p99 latency regression")])
        scan = AsyncMock(return_value=[])
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "p99 latency" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert [r["virtual_record_id"] for r in result] == ["vr-1"]
        scan.assert_not_awaited()
        # Answered by the index alone on a large store: the note says what it cannot see.
        note = render_pattern_match_coverage_note(coverage)
        assert "incomplete" not in note and "title or metadata" in note

    @pytest.mark.asyncio
    async def test_a_regex_with_no_word_is_scanned_and_marked(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "p99 latency regression")])
        scan = AsyncMock(return_value=[{"virtual_record_id": "vr-2", "matched_terms": "1"}])
        coverage = PatternMatchCoverage()
        regex_only = 'grep -rciE "[0-9]{3}-[0-9]{4}" .'
        with _llm_commands('grep -rci "p99 latency" .', regex_only), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert {r["virtual_record_id"] for r in result} == {"vr-1", "vr-2"}
        assert [c.kwargs["grep_command"] for c in scan.await_args_list] == [regex_only]
        note = render_pattern_match_coverage_note(coverage)
        assert "no whole word to look up" in note and "time-bounded scan" in note

    @pytest.mark.asyncio
    async def test_an_index_that_fails_falls_back_to_the_scan_and_says_so(self) -> None:
        searcher = InvertedIndexSearcher([])
        searcher.search_lexical = AsyncMock(side_effect=RuntimeError("vector store down"))
        scan = AsyncMock(return_value=[{"virtual_record_id": "vr-2"}])
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "p99 latency" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert [r["virtual_record_id"] for r in result] == ["vr-2"]
        assert "keyword index could not be searched" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_the_keyword_fallback_is_looked_up_too(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "notes on ERR_GATEWAY_7731")])
        scan = AsyncMock(return_value=[])
        with _llm_commands('grep -rci "nothing matches this" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan):
            result = await run_pattern_match_with_llm_grep(**_kwargs(llm=MagicMock()), retrieval_service=searcher)

        assert [r["virtual_record_id"] for r in result] == ["vr-1"]
        # Only the command the index found nothing for was scanned.
        assert [c.kwargs["grep_command"] for c in scan.await_args_list] == ['grep -rci "nothing matches this" .']
        assert searcher.calls[-1]["queries"] == ["err_gateway_7731 rollback"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["false", "0", "off"])
    async def test_the_index_can_be_switched_off(self, value: str) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "p99 latency")])
        scan = AsyncMock(return_value=[])
        with _llm_commands('grep -rci "p99 latency" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan), \
                patch.dict(os.environ, {"PATTERN_MATCH_INDEX": value}):
            await run_pattern_match_with_llm_grep(**_kwargs(llm=MagicMock()), retrieval_service=searcher)

        assert scan.await_count >= 1
        assert searcher.calls == []

    @pytest.mark.asyncio
    async def test_a_store_without_a_lexical_index_scans_as_before(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "p99 latency")])
        searcher.lexical_search_available = AsyncMock(return_value=False)
        scan = AsyncMock(return_value=[{"virtual_record_id": "vr-2"}])
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "p99 latency" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan):
            await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        scan.assert_awaited_once()
        assert searcher.calls == []
        assert render_pattern_match_coverage_note(coverage) == ""

    @pytest.mark.asyncio
    async def test_a_capped_lookup_marks_the_search_incomplete(self) -> None:
        searcher = InvertedIndexSearcher([(f"vr-{i}", "latency spike") for i in range(400)])
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "latency" .'):
            await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert "only the best-ranked were checked" in render_pattern_match_coverage_note(coverage)


@pytest.mark.asyncio
async def test_a_short_wait_still_keeps_index_hits() -> None:
    from app.utils.pattern_match import await_pattern_match

    searcher = InvertedIndexSearcher([("vr-1", "p99 latency")])
    found: dict = {}
    with _llm_commands('grep -rci "p99 latency" .'):
        task = asyncio.create_task(run_pattern_match_with_llm_grep(
            **_kwargs(llm=MagicMock()), retrieval_service=searcher, found=found,
        ))
        result = await await_pattern_match(task, MagicMock(), wait_budget=2.0, found_so_far=found)

    assert [r["virtual_record_id"] for r in result] == ["vr-1"]


class TestIndexNotes:
    @pytest.mark.asyncio
    async def test_more_matches_than_listed_is_said(self) -> None:
        searcher = InvertedIndexSearcher([(f"vr-{i}", "zebrafinch sighting") for i in range(60)])
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "zebrafinch" .'):
            await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert "more records matched than are listed" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_a_slow_check_keeps_the_verified_hits_and_says_so(self) -> None:
        from app.utils import exact_text_index

        searcher = InvertedIndexSearcher([("vr-fast", "p99 latency"), ("vr-slow", "p99 latency slow")])
        scan = AsyncMock(return_value=[])
        coverage = PatternMatchCoverage()
        real = exact_text_index._stage_matches
        with _llm_commands('grep -rci "p99 latency" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan), \
                patch.object(exact_text_index, "_stage_matches",
                             lambda stage, text: None if "slow" in text else real(stage, text)):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert [r["virtual_record_id"] for r in result] == ["vr-fast"]
        scan.assert_not_awaited()
        assert "took too long to check" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_collections_the_index_could_not_search_are_said(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "p99 latency")])
        lookup = searcher.search_lexical

        async def _partly(*args: object, **kwargs: object) -> object:
            result = await lookup(*args, **kwargs)
            result.failed_collections, result.searched_collections = 1, 3
            return result

        searcher.search_lexical = _partly
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "p99" .'):
            await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert "could not search 1 of its 3 collections" in render_pattern_match_coverage_note(coverage)


def _store(tmp_path, records: list[dict]) -> list[tuple[str, str]]:
    """Write *records* ({vrid, name, text}) as stored record files; return their chunks."""
    connector_dir = tmp_path / _ORG / "PipesHub" / "records" / _CONNECTOR
    chunks = []
    for i, rec in enumerate(records):
        d = connector_dir / f"d{i}"
        d.mkdir(parents=True)
        body = {"record": {"record_name": rec["name"], "block_containers": {"blocks": [{"data": rec["text"]}]}}}
        (d / f"record_{rec['vrid']}.json").write_text(json.dumps(body))
        chunks.append((rec["vrid"], rec["text"]))
    return chunks


_SUBSTRING_RECORD = {
    "vrid": "aaaaaaaa-0000-0000-0000-000000000001", "name": "gateway notes",
    "text": "the proxy logged ERR_CONN_RESET twice",
}
_TITLE_RECORD = {
    "vrid": "aaaaaaaa-0000-0000-0000-000000000002", "name": "Quokkaflux rollout plan",
    "text": "steps for the migration window",
}
_TOKEN_RECORD = {
    "vrid": "aaaaaaaa-0000-0000-0000-000000000003", "name": "cache notes",
    "text": "warm the cache before the release",
}
_INSIDE_WORD_RECORD = {
    "vrid": "aaaaaaaa-0000-0000-0000-000000000004", "name": "layer notes",
    "text": "the precache layer fills at boot",
}


class TestWhatOnlyGrepFinds:
    """grep matched substrings of the whole stored file; the index matches
    whole words of passage text. What only grep finds must not disappear."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("command", "expected"), [
        ('grep -rci "CONN_RESET" .', _SUBSTRING_RECORD["vrid"]),  # inside a longer token
        ('grep -rci "quokkaflux" .', _TITLE_RECORD["vrid"]),  # only in the record's title
    ])
    async def test_an_index_miss_is_scanned_on_a_large_store(self, tmp_path, command: str, expected: str) -> None:
        chunks = _store(tmp_path, [_SUBSTRING_RECORD, _TITLE_RECORD, _TOKEN_RECORD])
        searcher = InvertedIndexSearcher(chunks, store_chunks=10_000_000)
        coverage = PatternMatchCoverage()
        with patch("app.agents.actions.storage_search.storage_search._resolve_mount_root", return_value=str(tmp_path)), \
                _llm_commands(command):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert [r["virtual_record_id"] for r in result] == [expected]
        assert "run again as a time-bounded scan" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_a_small_store_scans_every_command_too(self, tmp_path) -> None:
        chunks = _store(tmp_path, [_TOKEN_RECORD, _INSIDE_WORD_RECORD])
        searcher = InvertedIndexSearcher(chunks, store_chunks=len(chunks))
        coverage = PatternMatchCoverage()
        with patch("app.agents.actions.storage_search.storage_search._resolve_mount_root", return_value=str(tmp_path)), \
                _llm_commands('grep -rci "cache" .'):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        # The index finds the whole word; the scan adds "precache", as grep did.
        assert {r["virtual_record_id"] for r in result} == {_TOKEN_RECORD["vrid"], _INSIDE_WORD_RECORD["vrid"]}
        assert render_pattern_match_coverage_note(coverage) == ""

    @pytest.mark.asyncio
    async def test_a_large_store_answered_by_the_index_says_what_it_cannot_see(self, tmp_path) -> None:
        chunks = _store(tmp_path, [_TOKEN_RECORD, _INSIDE_WORD_RECORD])
        searcher = InvertedIndexSearcher(chunks, store_chunks=10_000_000)
        scan = AsyncMock(return_value=[])
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "cache" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", scan):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert [r["virtual_record_id"] for r in result] == [_TOKEN_RECORD["vrid"]]
        scan.assert_not_awaited()
        assert "text inside a longer word" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("size", "small"), [(None, False), (200_000, True), (200_001, False)])
    async def test_the_store_size_rule(self, size: int | None, small: bool) -> None:
        from app.utils.pattern_match import _is_small_store

        searcher = InvertedIndexSearcher([], store_chunks=size)

        assert await _is_small_store(searcher, _ORG, "u1", logging.getLogger("test")) is small


class TestLookupsBeforeScans:
    """A scan of a missed command can take the rest of the budget, so the
    keyword lookup must not wait behind it."""

    @staticmethod
    async def _blocking_scan(**_kwargs: object) -> list:
        await asyncio.sleep(30)
        return []

    @pytest.mark.asyncio
    async def test_the_keyword_lookup_answers_before_a_slow_scan_under_the_budget(self) -> None:
        searcher = InvertedIndexSearcher([("vr-1", "notes on ERR_GATEWAY_7731")])
        coverage = PatternMatchCoverage()
        with _llm_commands('grep -rci "nothing matches this" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", side_effect=self._blocking_scan), \
                patch("app.utils.pattern_match._PATTERN_MATCH_TOTAL_BUDGET", 0.5):
            result = await run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), coverage=coverage, retrieval_service=searcher,
            )

        assert [r["virtual_record_id"] for r in result] == ["vr-1"]
        assert "time limit" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_the_keyword_lookup_answers_before_a_slow_scan_under_a_short_wait(self) -> None:
        from app.utils.pattern_match import await_pattern_match

        searcher = InvertedIndexSearcher([("vr-1", "notes on ERR_GATEWAY_7731")])
        found: dict = {}
        with _llm_commands('grep -rci "nothing matches this" .'), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", side_effect=self._blocking_scan):
            task = asyncio.create_task(run_pattern_match_with_llm_grep(
                **_kwargs(llm=MagicMock()), retrieval_service=searcher, found=found,
            ))
            result = await await_pattern_match(task, MagicMock(), wait_budget=0.5, found_so_far=found)

        assert [r["virtual_record_id"] for r in result] == ["vr-1"]


@pytest.mark.asyncio
async def test_a_small_store_scans_the_keyword_fallback_even_after_its_lookup_hit(tmp_path) -> None:
    chunks = _store(tmp_path, [_TOKEN_RECORD, _INSIDE_WORD_RECORD])
    searcher = InvertedIndexSearcher(chunks, store_chunks=len(chunks))
    with patch("app.agents.actions.storage_search.storage_search._resolve_mount_root", return_value=str(tmp_path)), \
            _llm_commands('grep -rci "nothing matches this" .'):
        result = await run_pattern_match_with_llm_grep(
            **_kwargs(llm=MagicMock(), query="cache"), retrieval_service=searcher,
        )

    # The lookup finds the whole word; the keyword grep adds "precache".
    assert {r["virtual_record_id"] for r in result} == {_TOKEN_RECORD["vrid"], _INSIDE_WORD_RECORD["vrid"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("scanned", [[], [{"virtual_record_id": _INSIDE_WORD_RECORD["vrid"], "matched_terms": "1"}]])
async def test_the_keyword_scan_adds_to_its_lookup_hits_and_never_replaces_them(scanned: list) -> None:
    searcher = InvertedIndexSearcher(
        [(_TOKEN_RECORD["vrid"], _TOKEN_RECORD["text"]), (_INSIDE_WORD_RECORD["vrid"], _INSIDE_WORD_RECORD["text"])],
        store_chunks=2,
    )
    with _llm_commands('grep -rci "nothing matches this" .'), \
            patch("app.utils.pattern_match.execute_pattern_match_pipeline", AsyncMock(return_value=scanned)):
        result = await run_pattern_match_with_llm_grep(
            **_kwargs(llm=MagicMock(), query="cache"), retrieval_service=searcher,
        )

    vrids = {r["virtual_record_id"] for r in result}
    assert _TOKEN_RECORD["vrid"] in vrids
    assert vrids == {_TOKEN_RECORD["vrid"], *(r["virtual_record_id"] for r in scanned)}


@pytest.mark.asyncio
async def test_a_keyword_scan_that_raises_keeps_its_lookup_hits() -> None:
    searcher = InvertedIndexSearcher([(_TOKEN_RECORD["vrid"], _TOKEN_RECORD["text"])], store_chunks=1)
    coverage = PatternMatchCoverage()
    with _llm_commands('grep -rci "nothing matches this" .'), \
            patch("app.utils.pattern_match.execute_pattern_match_pipeline",
                  AsyncMock(side_effect=[[], RuntimeError("grep blew up")])):
        result = await run_pattern_match_with_llm_grep(
            **_kwargs(llm=MagicMock(), query="cache"), coverage=coverage, retrieval_service=searcher,
        )

    assert [r["virtual_record_id"] for r in result] == [_TOKEN_RECORD["vrid"]]
    assert "keyword search failed" in render_pattern_match_coverage_note(coverage)

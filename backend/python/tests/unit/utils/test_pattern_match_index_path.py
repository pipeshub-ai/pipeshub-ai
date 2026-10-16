"""The grep layer answers from the lexical index, and scans files only for
what the index cannot express, saying so."""

import asyncio
import json
import logging
import os
import random
import subprocess
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


def _grep_seconds(connector_dir) -> float:
    started = time.perf_counter()
    subprocess.run(["grep", "-rci", "err_gateway_7731", "."], cwd=connector_dir, capture_output=True, check=False)
    return time.perf_counter() - started


@pytest.fixture
def large_store(tmp_path) -> tuple:
    """A blob store big enough that one grep over it takes 0.1 s or more (as a
    100k-record store takes tens of seconds), the needle written last."""
    rng = random.Random(3)
    words = [f"w{i}" for i in range(3000)]
    connector_dir = tmp_path / _ORG / "PipesHub" / "records" / _CONNECTOR
    chunks: list[tuple[str, str]] = []

    def write(i: int, text: str) -> None:
        vrid = f"{i:08x}-0000-0000-0000-000000000000"
        d = connector_dir / f"g{i % 40}" / f"d{i}"
        d.mkdir(parents=True)
        (d / f"record_{vrid}.json").write_text(json.dumps({"record": {"block_containers": {"blocks": [{"data": text}]}}}))
        chunks.append((vrid, text))

    size = 0
    while size < 32_000 and (size == 0 or _grep_seconds(connector_dir) < 0.1):
        for i in range(size, size + 4000):
            write(i, " ".join(rng.choices(words, k=300)))
        size += 4000
    write(size, _NEEDLE_TEXT)
    return tmp_path, connector_dir, chunks, chunks[-1][0]


class TestLargeStore:
    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_a_hit_the_scan_cannot_reach_in_its_budget_is_found_through_the_index(self, large_store) -> None:
        mount, connector_dir, chunks, needle_vrid = large_store
        # Well under what one full scan of this store takes, as at scale.
        budget = _grep_seconds(connector_dir) / 3

        with patch("app.agents.actions.storage_search.storage_search._resolve_mount_root", return_value=str(mount)), \
                patch("app.utils.pattern_match._PATTERN_MATCH_TOTAL_BUDGET", budget):
            scan_coverage = PatternMatchCoverage()
            scanned = await run_pattern_match_with_llm_grep(**_kwargs(), coverage=scan_coverage)
            index_coverage = PatternMatchCoverage()
            searcher = InvertedIndexSearcher(chunks)
            looked_up = await run_pattern_match_with_llm_grep(
                **_kwargs(), coverage=index_coverage, retrieval_service=searcher,
            )

        assert needle_vrid not in {r["virtual_record_id"] for r in scanned}
        assert "time limit" in render_pattern_match_coverage_note(scan_coverage)
        assert [r["virtual_record_id"] for r in looked_up] == [needle_vrid]
        assert index_coverage.complete
        assert render_pattern_match_coverage_note(index_coverage) == ""


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
        assert render_pattern_match_coverage_note(coverage) == ""

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
        scan.assert_not_awaited()
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

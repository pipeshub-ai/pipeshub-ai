"""An exact-text search cut short by a limit must say so.

Every limit below used to return what had been found with nothing but a log
line, so the tool result read as if the whole store had been searched.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.pattern_match import (
    PatternMatchCoverage,
    await_pattern_match,
    render_pattern_match_coverage_note,
    run_pattern_match,
    run_pattern_match_with_llm_grep,
)


def _storage(find_records: AsyncMock) -> MagicMock:
    storage = MagicMock()
    storage.find_records = find_records
    return storage


async def _run(find_records: AsyncMock, connector_ids: list[str], **kwargs) -> PatternMatchCoverage:
    coverage = PatternMatchCoverage()
    with patch("app.utils.pattern_match.StoragePatternMatch", return_value=_storage(find_records)):
        await run_pattern_match(
            config_service=AsyncMock(), org_id="org-1", user_id="user-1",
            graph_provider=AsyncMock(), command='grep -rci "p99" .',
            connector_ids=connector_ids, logger_instance=MagicMock(),
            coverage=coverage, **kwargs,
        )
    return coverage


def _found(records: list[dict], **extra) -> tuple[bool, str]:
    return True, json.dumps({"records": records, "total_found": len(records), **extra})


class TestRunPatternMatchCoverage:
    @pytest.mark.asyncio
    async def test_a_grep_that_timed_out_is_not_read_as_no_match(self) -> None:
        find = AsyncMock(side_effect=[
            _found([{"virtual_record_id": "vr-1"}]),
            (False, "Error: command timed out after 30s"),
        ])

        coverage = await _run(find, ["fast", "slow"])

        assert coverage.sources == {"fast", "slow"}
        assert coverage.timed_out == {"slow"}
        note = render_pattern_match_coverage_note(coverage)
        assert "searched 1 of 2 sources in full" in note
        assert "1 source(s) hit the time limit" in note

    @pytest.mark.asyncio
    async def test_a_connector_cut_off_by_its_own_timeout_is_marked(self) -> None:
        async def _find(**kwargs: object) -> tuple[bool, str]:
            if kwargs["connector_id"] == "slow":
                await asyncio.sleep(3600)
            return _found([])

        coverage = await _run(AsyncMock(side_effect=_find), ["fast", "slow"], timeout=0.05)

        assert coverage.timed_out == {"slow"}
        assert not coverage.complete

    @pytest.mark.asyncio
    async def test_a_failed_grep_is_marked(self) -> None:
        coverage = await _run(AsyncMock(return_value=(False, "Command failed (exit 2). Its output is withheld.")), ["c1"])

        assert coverage.failed == {"c1"}
        assert "1 source(s) could not be searched" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply", [
        _found([{"virtual_record_id": "vr-1"}], total_found=25),
        _found([{"virtual_record_id": "vr-1"}], output_truncated=True),
        (True, json.dumps({"records": [], "total_found": 0, "output_truncated": True})),
    ])
    async def test_more_matches_than_were_listed_is_marked(self, reply) -> None:
        coverage = await _run(AsyncMock(return_value=reply), ["c1"])

        assert coverage.capped == {"c1"}
        assert "matched more than could be listed" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_the_grep_is_told_its_line_cap(self) -> None:
        find = AsyncMock(return_value=_found([]))

        await _run(find, ["c1"])

        assert find.await_args.kwargs["max_output_lines"] == 200

    @pytest.mark.asyncio
    async def test_a_search_that_covered_everything_adds_no_note(self) -> None:
        coverage = await _run(AsyncMock(return_value=_found([{"virtual_record_id": "vr-1"}])), ["c1", "c2"])

        assert coverage.complete
        assert render_pattern_match_coverage_note(coverage) == ""


def _llm_grep_kwargs() -> dict:
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={"storageType": "local"})
    return {
        "query": "p99 latency regression after the cache change", "config_service": config_service,
        "org_id": "org-1", "user_id": "user-1", "graph_provider": AsyncMock(), "filters": None,
        "logger_instance": MagicMock(),
    }


class TestWholeSearchCutShort:
    @pytest.mark.asyncio
    async def test_the_total_budget_running_out_is_marked(self) -> None:
        async def _hang(**_kwargs: object) -> list:
            await asyncio.sleep(3600)
            return []

        coverage = PatternMatchCoverage()
        with patch("app.utils.pattern_match._PATTERN_MATCH_TOTAL_BUDGET", 0.05), \
                patch("app.utils.pattern_match.execute_pattern_match_pipeline", side_effect=_hang):
            await run_pattern_match_with_llm_grep(**_llm_grep_kwargs(), llm=None, coverage=coverage)

        assert coverage.stopped is not None
        assert "time limit" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_a_cancelled_run_marks_the_connectors_it_never_finished(self) -> None:
        async def _find(**_kwargs: object) -> tuple[bool, str]:
            await asyncio.sleep(3600)
            return _found([])

        coverage = PatternMatchCoverage()
        with patch("app.utils.pattern_match.StoragePatternMatch", return_value=_storage(AsyncMock(side_effect=_find))):
            task = asyncio.create_task(run_pattern_match(
                config_service=AsyncMock(), org_id="org-1", user_id="user-1",
                graph_provider=AsyncMock(), command='grep -rci "p99" .',
                connector_ids=["c1"], logger_instance=MagicMock(), coverage=coverage,
            ))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert coverage.timed_out == {"c1"}

    @pytest.mark.asyncio
    async def test_a_short_wait_that_stops_early_is_marked(self) -> None:
        async def _slow() -> list:
            await asyncio.sleep(3600)
            return []

        coverage = PatternMatchCoverage()
        task = asyncio.create_task(_slow())
        result = await await_pattern_match(
            task, MagicMock(), wait_budget=0.05, found_so_far={"vr": {"virtual_record_id": "vr"}},
            coverage=coverage,
        )

        assert result == [{"virtual_record_id": "vr"}]
        assert "stopped waiting" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_a_failed_task_is_marked(self) -> None:
        async def _boom() -> list:
            raise RuntimeError("grep blew up")

        coverage = PatternMatchCoverage()
        assert await await_pattern_match(asyncio.create_task(_boom()), MagicMock(), coverage=coverage) == []
        assert coverage.stopped == "it failed before it finished"


class TestMergeCap:
    """Each command can stay under its own cap while all of them together pass
    the merge's, which then drops records."""

    @staticmethod
    async def _merge(raw: list[dict], coverage: PatternMatchCoverage, **kwargs) -> None:
        from app.utils.pattern_match import merge_pattern_match_results

        graph = AsyncMock()
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={})
        await merge_pattern_match_results(
            raw_records=raw, virtual_record_id_to_result={}, user_id="u1", org_id="o1",
            blob_store=AsyncMock(), graph_provider=graph, is_multimodal_llm=False,
            logger_instance=MagicMock(), coverage=coverage, **kwargs,
        )

    @pytest.mark.asyncio
    async def test_records_dropped_by_the_merge_cap_are_reported(self) -> None:
        coverage = PatternMatchCoverage()
        raw = [{"virtual_record_id": f"vr-a{i}"} for i in range(40)] + [{"virtual_record_id": f"vr-b{i}"} for i in range(40)]

        await self._merge(raw, coverage)

        assert coverage.records_truncated
        assert "more records matched than are listed" in render_pattern_match_coverage_note(coverage)

    @pytest.mark.asyncio
    async def test_records_within_the_cap_leave_the_search_complete(self) -> None:
        coverage = PatternMatchCoverage()

        await self._merge([{"virtual_record_id": f"vr-{i}"} for i in range(50)], coverage)

        assert coverage.complete

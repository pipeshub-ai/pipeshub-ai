"""find_records with rank_all_output: rank every listed record before the
permission check, check in rank order and in batches, and narrow output cut at
its size limit by the pattern's rarest alternatives.

Output cut at a size limit keeps only the first files in directory order, so a
record later in the walk was never even ranked.
"""

import json
import shutil
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.storage_search import storage_search as ss
from app.agents.actions.storage_search.storage_search import (
    StoragePatternMatch,
    _grep_search_regexes,
    _narrowed_command,
    _rank_candidates,
    _rarest_alternatives,
    _validate_command,
)

needs_grep = pytest.mark.skipif(
    shutil.which("grep") is None or shutil.which("xargs") is None, reason="needs grep and xargs",
)


def _stored(text: str, name: str = "stored name") -> str:
    return json.dumps({"record": {"record_name": name, "block_containers": {"blocks": [{"data": text}]}}})


def _vrid(i: int) -> str:
    return f"{i:08x}-0000-0000-0000-000000000000"


def _write(directory: Path, i: int, text: str, name: str = "stored name") -> tuple[str, str, int]:
    (directory / f"record_{_vrid(i)}.json").write_text(_stored(text, name))
    return (f"./record_{_vrid(i)}.json", _vrid(i), 1)


class TestRanking:
    def test_two_workers_rank_like_one(self, tmp_path: Path) -> None:
        candidates = [_write(tmp_path, i, "ledger " * (i % 7) + ("audit" if i % 3 else "")) for i in range(40)]
        regexes = _grep_search_regexes('grep -rci "ledger\\|audit" .')

        assert _rank_candidates(candidates, str(tmp_path), regexes) == _rank_candidates(
            candidates, str(tmp_path), regexes, workers=2,
        )

    def test_the_byte_budget_is_shared_by_the_workers(self, tmp_path: Path) -> None:
        candidates = [_write(tmp_path, i, "ledger " * 50) for i in range(20)]
        regexes = _grep_search_regexes('grep -rci "ledger" .')

        with patch.object(ss, "_MAX_SCORE_TOTAL_BYTES", 1):
            ranked = _rank_candidates(candidates, str(tmp_path), regexes, workers=2)

        scored = [r for r in ranked if r[2] > 0]
        assert 1 <= len(scored) <= 2, "at most one file per worker reads past a spent budget"


class TestRarestAlternatives:
    def test_rare_terms_seen_in_the_sample_come_first(self, tmp_path: Path) -> None:
        candidates = [
            _write(tmp_path, i, "ledger common" + (" pylon" if i == 3 else "") + (" quorum" if i < 3 else ""))
            for i in range(10)
        ]
        regexes = _grep_search_regexes('grep -rci "ledger\\|quorum\\|pylon\\|nimbus" .')

        rare = _rarest_alternatives(candidates, str(tmp_path), regexes, 3)

        assert [rx.pattern for rx in rare] == ["pylon", "quorum", "nimbus"]

    def test_terms_in_half_the_sample_never_narrow(self, tmp_path: Path) -> None:
        candidates = [_write(tmp_path, i, "ledger" if i % 2 else "audit") for i in range(10)]
        regexes = _grep_search_regexes('grep -rci "ledger\\|audit" .')

        assert _rarest_alternatives(candidates, str(tmp_path), regexes, 3) == []


class TestNarrowedCommand:
    def test_rare_terms_select_the_files_the_command_then_reads(self) -> None:
        command = "grep -rliZ \"ledger\\|audit\" . | xargs -0 grep -Hci \"quorum\" | grep -v ':0$'"
        rare = _grep_search_regexes('grep -rci "pylon\\|cold[- ]start" .')

        narrowed = _narrowed_command(command, rare)

        assert narrowed == (
            "grep -rlZiE -e pylon -e 'cold[- ]start' . | xargs -0 -r grep -H -liZ 'ledger\\|audit' "
            "| xargs -0 grep -Hci \"quorum\" | grep -v ':0$'"
        )
        assert _validate_command(narrowed)[0]

    def test_a_first_stage_that_is_not_grep_is_not_narrowed(self) -> None:
        assert _narrowed_command('find . -name "*.json"', _grep_search_regexes('grep -rci "pylon" .')) is None


def _tool(graph: MagicMock, directory: Path) -> StoragePatternMatch:
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={"storageType": "local"})
    tool = StoragePatternMatch({"org_id": "org", "user_id": "u", "config_service": config, "graph_provider": graph})
    tool._resolve_connector_path = AsyncMock(return_value=(str(directory), None))
    return tool


def _graph(readable: set[str] | None = None) -> MagicMock:
    graph = MagicMock()

    async def _filter(vrids: list[str], *_args: object, **_kwargs: object) -> dict[str, str]:
        return {v: f"rid-{v}" for v in vrids if readable is None or v in readable}

    async def _records(record_ids: list[str], org_id: str) -> list[dict[str, str]]:
        return [{"_key": rid, "recordName": rid} for rid in record_ids]

    graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=_filter)
    graph.get_records_by_record_ids = AsyncMock(side_effect=_records)
    return graph


@needs_grep
class TestFindRecordsRankingTheWholeOutput:
    @pytest.fixture(autouse=True)
    def _runs_without_landlock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Confined where the kernel has Landlock; elsewhere find_records would refuse to run.
        monkeypatch.setenv("STORAGE_SEARCH_ALLOW_UNCONFINED", "true")

    @pytest.mark.asyncio
    async def test_a_cut_output_is_narrowed_by_the_rarest_term(self, tmp_path: Path) -> None:
        for i in range(60):
            _write(tmp_path, i, "ledger report")
        _write(tmp_path, 999, "ledger report on the quorum outage")
        tool = _tool(_graph(), tmp_path)

        with patch.object(ss, "_run_subprocess", wraps=ss._run_subprocess) as run:
            ok, output = await tool.find_records(
                "c1", "grep -rci \"ledger\\|quorum\" . | grep -v ':0$'", max_results=3,
                max_output_chars=400, rank_all_output=True, deadline=time.monotonic() + 30,
            )

        assert ok
        assert run.await_count == 2, "a second pass over the files holding the rare term"
        assert json.loads(output)["records"][0]["virtual_record_id"] == _vrid(999)

    @pytest.mark.asyncio
    async def test_no_narrowing_without_time_for_it(self, tmp_path: Path) -> None:
        for i in range(20):
            _write(tmp_path, i, "ledger report")
        tool = _tool(_graph(), tmp_path)

        with patch.object(ss, "_run_subprocess", wraps=ss._run_subprocess) as run:
            await tool.find_records(
                "c1", "grep -rci \"ledger\\|quorum\" . | grep -v ':0$'",
                max_output_chars=400, rank_all_output=True, deadline=time.monotonic() + 1,
            )

        assert run.await_count == 1

    @pytest.mark.asyncio
    async def test_permission_checks_run_in_rank_order_until_enough(self, tmp_path: Path) -> None:
        # Distinct scores, best first: 9, 8, 7, 6 (both terms), then 5 down to 0.
        for i in range(10):
            _write(tmp_path, i, " ledger" * (i + 1) if i < 6 else "ledger" + " audit" * (i - 5))
        graph = _graph(readable={_vrid(1), _vrid(2), _vrid(7)})
        tool = _tool(graph, tmp_path)

        with patch.object(ss, "_PERMISSION_CHECK_BATCH", 3):
            ok, output = await tool.find_records(
                "c1", 'grep -rci "ledger\\|audit" .', max_results=2, rank_all_output=True,
            )

        assert ok
        assert [r["virtual_record_id"] for r in json.loads(output)["records"]] == [_vrid(7), _vrid(2)]
        checked = [c.args[0] for c in graph.filter_accessible_virtual_record_ids.await_args_list]
        assert checked == [
            [_vrid(9), _vrid(8), _vrid(7)], [_vrid(6), _vrid(5), _vrid(4)], [_vrid(3), _vrid(2), _vrid(1)],
        ], "in rank order, and no fourth batch once two readable records are found"

    @pytest.mark.asyncio
    async def test_a_failed_later_batch_fails_closed(self, tmp_path: Path) -> None:
        for i in range(6):
            _write(tmp_path, i, "ledger")
        graph = _graph(readable=set())
        graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=[{}, RuntimeError("down")])
        tool = _tool(graph, tmp_path)

        with patch.object(ss, "_PERMISSION_CHECK_BATCH", 3):
            ok, output = await tool.find_records("c1", 'grep -rci "ledger" .', rank_all_output=True)

        assert not ok
        assert "Refusing" in output

    @pytest.mark.asyncio
    async def test_other_storage_keeps_only_text_matches(self, tmp_path: Path) -> None:
        _write(tmp_path, 1, "the quorum vote is due in April")
        _write(tmp_path, 2, "fuel invoices", name="Quorum minutes")
        tool = _tool(_graph(), tmp_path)

        ok, output = await tool.find_records(
            "c1", 'grep -rli "quorum" .', scope_connector_ids=frozenset({"c0"}), content_owner=True,
            rank_all_output=True,
        )

        assert ok
        assert [r["virtual_record_id"] for r in json.loads(output)["records"]] == [_vrid(1)]

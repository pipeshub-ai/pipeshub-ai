"""Explicit grep commands, a per-connector record cap and the readable-only view (the exact-text search API)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import pattern_match as pm
from app.utils.pattern_match import run_pattern_match

ORG, USER = "org-1", "user-1"


def _found(n: int) -> tuple[bool, str]:
    records = [{"virtual_record_id": f"vr-{i}"} for i in range(n)]
    return True, json.dumps({"records": records, "total_found": n})


async def _run(find: AsyncMock, connector_ids: list[str], **kwargs: object) -> None:
    storage = MagicMock()
    storage.find_records = find
    with patch("app.utils.pattern_match.StoragePatternMatch", return_value=storage):
        await run_pattern_match(
            config_service=AsyncMock(), org_id=ORG, user_id=USER, graph_provider=AsyncMock(),
            command='grep -rciF -e "x" .', connector_ids=connector_ids, logger_instance=MagicMock(), **kwargs,
        )


async def test_explicit_commands_run_as_given_and_never_fall_back_to_keywords() -> None:
    calls: list[str | None] = []

    async def pipeline(**kwargs: object) -> list[dict]:
        calls.append(kwargs.get("grep_command"))
        return []

    with patch.object(pm, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(pm, "execute_pattern_match_pipeline", pipeline):
        found = await pm.run_pattern_match_with_llm_grep(
            query="invoice total", config_service=MagicMock(), org_id=ORG, user_id=USER,
            graph_provider=MagicMock(), filters={}, logger_instance=MagicMock(),
            commands=["grep -rciF -e nomatch ."],
        )
    # the command ran once, as given; a pattern that matches nothing returns nothing (no keyword grep, no LLM)
    assert found == [] and calls == ["grep -rciF -e nomatch ."]


@pytest.mark.parametrize("readable_only", [False, True])
async def test_the_readable_view_and_the_record_cap_reach_the_storage_search(readable_only) -> None:
    find = AsyncMock(return_value=_found(1))
    await _run(find, ["c1"], max_records_per_connector=7, readable_only=readable_only)
    assert find.await_count >= 1
    for call in find.await_args_list:
        assert call.kwargs["max_results"] == 7
    # the whole-connector grep is the one that reads only what the caller may read
    assert any(call.kwargs["readable_only"] is readable_only for call in find.await_args_list)


async def test_the_defaults_leave_the_existing_search_unchanged() -> None:
    find = AsyncMock(return_value=_found(1))
    await _run(find, ["c1"])
    for call in find.await_args_list:
        assert call.kwargs["max_results"] == pm._MAX_RECORDS_PER_CONNECTOR
        assert call.kwargs["readable_only"] is False


async def test_an_empty_command_list_is_an_error_not_a_silent_fallback_to_the_model() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        await pm.run_pattern_match_with_llm_grep(
            query="x", config_service=MagicMock(), org_id=ORG, user_id=USER,
            graph_provider=MagicMock(), filters={}, logger_instance=MagicMock(), commands=[],
        )


@pytest.mark.parametrize("commands", [[""], ["  "], ['grep -rl -e "x" .', "\t"]])
async def test_a_blank_command_is_an_error_not_a_silent_fallback_to_the_model(commands) -> None:
    with pytest.raises(ValueError, match="blank"):
        await pm.run_pattern_match_with_llm_grep(
            query="x", config_service=MagicMock(), org_id=ORG, user_id=USER,
            graph_provider=MagicMock(), filters={}, logger_instance=MagicMock(), commands=commands,
        )


@pytest.mark.parametrize("command", [
    'grep -riF -e "invoice" .',
    'grep -rl -e "a" . | xargs -0 grep -iF -e "b"',
    'grep -rn -e "a" .',
    "wc -l",
])
async def test_a_command_that_prints_matching_lines_is_rejected_naming_the_allowed_modes(command) -> None:
    with pytest.raises(ValueError, match="-l, --files-with-matches.*-c, --count|must end in a grep"):
        await pm.run_pattern_match_with_llm_grep(
            query="x", config_service=MagicMock(), org_id=ORG, user_id=USER,
            graph_provider=MagicMock(), filters={}, logger_instance=MagicMock(), commands=[command],
        )


@pytest.mark.parametrize("command", [
    'grep -rl -e "a" .', 'grep -rciF -e "a" .', 'grep --files-with-matches -r -e "a" .',
    'grep -rl -e "a" . | xargs -0 grep -c -e "b"',
])
async def test_listing_and_count_commands_are_accepted(command) -> None:
    with patch.object(pm, "check_pattern_match_eligible", AsyncMock(return_value=False)):
        assert await pm.run_pattern_match_with_llm_grep(
            query="x", config_service=MagicMock(), org_id=ORG, user_id=USER,
            graph_provider=MagicMock(), filters={}, logger_instance=MagicMock(), commands=[command],
        ) == []


async def test_a_path_argument_is_rejected_for_the_readable_view_and_kept_otherwise() -> None:
    command = 'grep -rl -e "a" ./team-A'
    with pytest.raises(ValueError, match="scope with filters, not paths"):
        await pm.run_pattern_match_with_llm_grep(
            query="x", config_service=MagicMock(), org_id=ORG, user_id=USER, graph_provider=MagicMock(),
            filters={}, logger_instance=MagicMock(), commands=[command], readable_only=True,
        )
    with patch.object(pm, "check_pattern_match_eligible", AsyncMock(return_value=False)):
        await pm.run_pattern_match_with_llm_grep(
            query="x", config_service=MagicMock(), org_id=ORG, user_id=USER, graph_provider=MagicMock(),
            filters={}, logger_instance=MagicMock(), commands=[command],
        )


def _rec(vrid: str, connector: str | None = None, count: int = 1) -> dict:
    rec = {"virtual_record_id": vrid, "match_count": str(count), "matched_terms": "1"}
    return {**rec, "connector_id": connector} if connector else rec


async def _merged(coverage: pm.PatternMatchCoverage, cap: int, *, index_hits=(), scans=()) -> list[dict]:
    async def through_index(cmds, searcher, *, found, **_kw) -> list[str]:
        pm._merge_command_hits(found, list(index_hits))
        return []

    scanned = iter(scans)

    async def pipeline(**_kw) -> list[dict]:
        return next(scanned)

    with patch.object(pm, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(pm, "_index_searcher", AsyncMock(return_value=MagicMock() if index_hits else None)), \
         patch.object(pm, "_is_small_store", AsyncMock(return_value=False)), \
         patch.object(pm, "_match_through_index", through_index), \
         patch.object(pm, "execute_pattern_match_pipeline", pipeline):
        return await pm.run_pattern_match_with_llm_grep(
            query="x", config_service=MagicMock(), org_id=ORG, user_id=USER, graph_provider=MagicMock(),
            filters={}, logger_instance=MagicMock(), commands=['grep -rl -e "a" .'] * max(len(scans), 1),
            coverage=coverage, max_records_per_connector=cap,
        )


async def test_the_record_cap_holds_over_index_hits_and_over_all_commands_together() -> None:
    cov = pm.PatternMatchCoverage()
    out = await _merged(cov, 1, index_hits=[_rec("i1", count=5), _rec("i2", count=4)])
    assert [r["virtual_record_id"] for r in out] == ["i1"] and cov.records_truncated

    cov = pm.PatternMatchCoverage()
    out = await _merged(cov, 1, scans=[[_rec("a", "c1", 3), _rec("x", "c2")], [_rec("b", "c1", 2)]])
    # one per connector, best first, and the connector that was cut is reported as capped
    assert sorted(r["virtual_record_id"] for r in out) == ["a", "x"] and cov.capped == {"c1"}

    cov = pm.PatternMatchCoverage()
    out = await _merged(cov, 5, scans=[[_rec("a", "c1")], [_rec("b", "c1")]])
    assert len(out) == 2 and cov.complete


def _containers() -> MagicMock:
    return MagicMock(
        app_ids_trusted=frozenset(), record_group_ids_trusted=frozenset({"rg1"}),
        direct_records=[], fallback_reason=None,
    )


async def _scoped_run(readable_only: bool, find: AsyncMock) -> AsyncMock:
    graph = AsyncMock()
    graph.get_accessible_containers.return_value = _containers()
    graph.get_record_group_virtual_record_ids.return_value = ["v1", "v2"]
    storage = MagicMock()
    storage.find_records = find
    storage._resolve_connector_path = AsyncMock(return_value=("/c", None))
    with patch("app.utils.pattern_match.StoragePatternMatch", return_value=storage), \
         patch.object(pm, "_fetch_container_group_rows", AsyncMock(return_value=[])), \
         patch.object(pm, "_accessible_groups_for_connector", return_value=[{"id": "rg1"}]), \
         patch.object(pm, "_resolve_search_paths", AsyncMock(return_value=["./g"])):
        await run_pattern_match(
            config_service=AsyncMock(), org_id=ORG, user_id=USER, graph_provider=graph,
            command='grep -rl -e "x" .', connector_ids=["c1"], logger_instance=MagicMock(),
            readable_only=readable_only,
        )
    return find


async def test_a_record_group_scoped_grep_reads_a_view_limited_to_the_groups_records() -> None:
    find = await _scoped_run(True, AsyncMock(return_value=_found(1)))
    assert find.await_count == 1
    call = find.await_args
    assert call.kwargs["readable_only"] is True and call.kwargs["allowed_vrids"] == frozenset({"v1", "v2"})
    assert call.kwargs["command"] == 'grep -rl -e "x" .'  # a flat view has no group paths to scope to


async def test_without_the_readable_view_the_scoped_grep_still_scopes_by_path() -> None:
    find = await _scoped_run(False, AsyncMock(return_value=_found(1)))
    call = find.await_args
    assert call.kwargs["readable_only"] is False and call.kwargs["allowed_vrids"] is None
    assert '"./g"' in call.kwargs["command"]

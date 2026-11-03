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

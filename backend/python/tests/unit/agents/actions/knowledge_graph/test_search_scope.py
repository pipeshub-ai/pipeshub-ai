"""``execute_search`` inside a scope its caller already resolved
(``scope_virtual_record_ids``), opened by the caller's ``preamble``."""

from __future__ import annotations

import json
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.search import (
    execute_search,
    with_json_preamble,
)
from tests.unit.agents.actions.knowledge_graph.test_search import (
    _RENDER_PATCHES,
    _found,
    _state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"


@pytest.fixture(autouse=True)
def _no_time_range() -> Iterator[None]:
    with patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None)):
        yield


async def _search(retrieval: AsyncMock, *, grep: AsyncMock | None = None, **kwargs: Any) -> str:  # noqa: ANN401
    with ExitStack() as stack:
        for render_patch in _RENDER_PATCHES:
            stack.enter_context(render_patch)
        stack.enter_context(patch(
            f"{_SEARCH}.run_pattern_match_with_llm_grep", grep or AsyncMock(return_value=[]),
        ))
        return await execute_search(_state(retrieval), "ACME-1042 renewal", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [[], [""], [None]])
async def test_an_empty_scope_searches_nothing(scope: list[Any]) -> None:
    retrieval = AsyncMock()
    payload = json.loads(await _search(retrieval, scope_virtual_record_ids=scope))
    assert payload["result_count"] == 0 and payload["status"] == "success"
    retrieval.search_with_filters.assert_not_called()


@pytest.mark.asyncio
async def test_the_search_runs_inside_the_scope_without_grep() -> None:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = _found()
    grep = AsyncMock(return_value=[])
    result = await _search(retrieval, grep=grep, scope_virtual_record_ids=["vr-1", "vr-1", "vr-2"])
    assert result.startswith("Top 1 block")
    assert retrieval.search_with_filters.call_args.kwargs["virtual_record_ids_from_tool"] == ["vr-1", "vr-2"]
    grep.assert_not_called()


@pytest.mark.asyncio
async def test_without_a_scope_the_search_is_unchanged() -> None:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = _found()
    grep = AsyncMock(return_value=[])
    result = await _search(retrieval, grep=grep)
    assert result.startswith("Top 1 block")
    assert retrieval.search_with_filters.call_args.kwargs["virtual_record_ids_from_tool"] is None
    grep.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_agent_filters_still_reach_retrieval_inside_a_scope() -> None:
    """The scope narrows; the agent's apps and knowledge bases still bound it,
    and retrieval intersects the ids with what the user can read."""
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = _found()
    await _search(retrieval, scope_virtual_record_ids=["vr-1"])
    kwargs = retrieval.search_with_filters.call_args.kwargs
    assert kwargs["filter_groups"]["apps"] == ["private-kb-app", "demo-connector", "wiki"]
    assert (kwargs["org_id"], kwargs["user_id"]) == ("o1", "u1")


@pytest.mark.asyncio
async def test_the_preamble_opens_a_rendered_result() -> None:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = _found()
    result = await _search(retrieval, scope_virtual_record_ids=["vr-1"], preamble="Entities:\n- Acme\n\n")
    assert result.startswith("Entities:\n- Acme\n\nTop 1 block")


@pytest.mark.asyncio
async def test_a_json_result_carries_the_preamble_as_a_field() -> None:
    retrieval = AsyncMock()
    payload = json.loads(await _search(retrieval, scope_virtual_record_ids=[], preamble="Entities:\n- Acme\n\n"))
    assert payload["entities"] == "Entities:\n- Acme" and payload["result_count"] == 0


def test_only_a_json_result_gets_the_preamble_as_a_field() -> None:
    assert with_json_preamble('{"status": "error"}', "") == '{"status": "error"}'
    assert with_json_preamble("Entities:\n\nTop 1 block", "Entities:\n\n") == "Entities:\n\nTop 1 block"
    assert with_json_preamble("{not json", "P\n") == "{not json"
    assert with_json_preamble("[1]", "P\n") == "[1]"
    assert json.loads(with_json_preamble('{"status": "error"}', "P\n")) == {"entities": "P", "status": "error"}


@pytest.mark.asyncio
async def test_a_scope_also_narrowed_by_entity_ids_keeps_only_their_overlap() -> None:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = _found()
    state = _state(retrieval)
    state["_kg_record_scoped_entities"] = {"rg-1": "record_group"}
    scope = MagicMock(virtual_ids=["vr-2", "vr-3"], truncated=False, entities_skipped=0)
    with ExitStack() as stack:
        for render_patch in _RENDER_PATCHES:
            stack.enter_context(render_patch)
        stack.enter_context(patch(f"{_SEARCH}.resolve_entity_virtual_ids", AsyncMock(return_value=scope)))
        stack.enter_context(patch(
            f"{_SEARCH}.resolve_record_scoped_entities", return_value=[("rg-1", "record_group")],
        ))
        stack.enter_context(patch(f"{_SEARCH}.unresolved_entity_ids", return_value=[]))
        await execute_search(state, "renewal", entity_ids=["rg-1"], scope_virtual_record_ids=["vr-1", "vr-2"])
    assert retrieval.search_with_filters.call_args.kwargs["virtual_record_ids_from_tool"] == ["vr-2"]


def test_a_result_with_images_is_left_as_it_is() -> None:
    from app.agent_loop_lib.core.messages import TextPart

    parts = [TextPart(text="Entities:\n\nTop 1 block")]
    assert with_json_preamble(parts, "Entities:\n\n") is parts

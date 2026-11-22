"""A failed neighbour walk or path lookup is logged once, by the code that handles it, without the graph's text."""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.code_graph import ops
from tests.support.graph_read_errors import SENTINELS, marked_internal, unmarked
from tests.support.log_capture import failure_line

pytestmark = pytest.mark.asyncio

OPS = "app.agents.actions.code_graph.ops"
BLOCKS = [{"_key": "b1"}, {"_key": "b2"}]


def _walk(**neighbours: object) -> MagicMock:
    graph = MagicMock()
    graph.get_neighbors_for_nodes_by_relationship_types = AsyncMock(**neighbours)
    return graph


@pytest.mark.parametrize(("error", "error_class", "level"), [
    (unmarked, "unavailable", logging.WARNING), (marked_internal, "internal", logging.ERROR),
], ids=["unrecorded", "client_recorded"])
async def test_a_failed_degree_walk_is_one_text_free_line(
    caplog: pytest.LogCaptureFixture, error: object, error_class: str, level: int,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=OPS):
        assert await ops._degrees(_walk(side_effect=error()), BLOCKS, ["CALLS"], "org-1", None) == ({}, False)
    failure_line(caplog.records, "code_graph.degrees.failed", error_class, level, *SENTINELS, handler="_degrees")
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == ["code_graph.degrees.failed"]


async def test_a_cursor_that_does_not_advance_is_one_error_line_naming_where(caplog: pytest.LogCaptureFixture) -> None:
    """Our provider bug: no cause, class internal, one text-free stack whose frames name the raise."""
    page = [{"collection": ops._BLOCKS, "key": f"k{i}", "anchorKey": "b1", "cursor": "same"} for i in range(ops._DEGREE_PAGE)]
    with caplog.at_level(logging.DEBUG, logger=OPS):
        assert await ops._degrees(_walk(return_value=page), BLOCKS, ["CALLS"], "org-1", None) == ({}, False)
    failure_line(caplog.records, "code_graph.degrees.failed", "internal", logging.ERROR)
    (line,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "_WalkFailed: <message omitted>" in line.exc_text
    assert "in _readable_neighbour_rows" in line.exc_text and "ops.py" in line.exc_text


async def test_the_walk_itself_raises_without_a_line(caplog: pytest.LogCaptureFixture) -> None:
    """Every other walker hands the failure to the tool's _run, which logs it once."""
    with caplog.at_level(logging.DEBUG, logger=OPS), pytest.raises(ops._WalkFailed):
        await ops._readable_neighbour_rows(_walk(side_effect=marked_internal()), "org-1", None, ["b1"], ["CALLS"], "any",
                                           page=10, budget=10)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("error", [unmarked, marked_internal], ids=["unrecorded", "client_recorded"])
async def test_a_failed_path_lookup_is_one_text_free_line(caplog: pytest.LogCaptureFixture, error: object) -> None:
    graph = MagicMock()
    graph.get_file_paths_for_records = AsyncMock(side_effect=error())
    with caplog.at_level(logging.DEBUG, logger=OPS):
        assert await ops.path_for_record(graph, "org-1", "rec-1") is None
    cls, level = ("unavailable", logging.WARNING) if error is unmarked else ("internal", logging.ERROR)
    fields = failure_line(caplog.records, "code_graph.path_lookup.failed", cls, level, *SENTINELS, handler="path_for_record")
    assert fields["record_id"] == "rec-1"


async def test_a_record_with_no_path_is_none_without_a_line(caplog: pytest.LogCaptureFixture) -> None:
    graph = MagicMock()
    graph.get_file_paths_for_records = AsyncMock(return_value={})
    with caplog.at_level(logging.DEBUG, logger=OPS):
        assert await ops.path_for_record(graph, "org-1", "rec-1") is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

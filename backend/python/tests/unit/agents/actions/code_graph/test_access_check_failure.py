"""An access check the graph could not answer hides the result, and is logged without the graph's text."""
import logging
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.code_graph.ops import _user_can_read
from app.utils.error_class import mark_error
from tests.support.log_capture import failure_line

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("stamped", [False, True], ids=["unrecorded", "db_layer_recorded"])
async def test_a_failed_access_check_reads_as_no_access(caplog: pytest.LogCaptureFixture, stamped: bool) -> None:
    """At WARNING either way: db.query.failed stamps the class, not a level already logged."""
    down = ConnectionError("graph down at 10.0.0.7:8529")
    if stamped:
        mark_error(down, "unavailable")
    graph = AsyncMock()
    graph.check_record_access_with_details = AsyncMock(side_effect=down)

    with caplog.at_level(logging.DEBUG, logger="app.agents.actions.code_graph.ops"):
        assert await _user_can_read(graph, "user-1", "org-1", "rec-1") is False

    fields = failure_line(caplog.records, "code_graph.access_check.failed", "unavailable", logging.WARNING, "10.0.0.7")
    assert fields["record_id"] == "rec-1"

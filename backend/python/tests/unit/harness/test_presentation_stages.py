"""A tool result's presentation is built stage by stage: one stage that cannot be built is logged and dropped,
never the artifacts and citation sources the others journal."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.presentation import SEARCH_TOOLS, PipesHubPresenter
from app.utils.chat_helpers import CitationRefMapper
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.types import ToolResult
from harness_core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    import pytest

URL = "https://kb.example/record/r1/block/0"


def _presenter() -> tuple[Any, PipesHubPresenter]:
    context = SimpleNamespace(tool_state={}, root_agent_spec=None, conversation_id=None)
    return context, PipesHubPresenter(context, ToolRegistry(), citations=CitationJournal(context))


async def test_m6_1_a_stage_that_fails_keeps_the_result_and_citation_sources(caplog: pytest.LogCaptureFixture) -> None:
    context, presenter = _presenter()
    mapper = CitationRefMapper()
    context.tool_state["citation_ref_mapper"] = mapper
    _ = mapper.get_or_create_ref(URL)
    call = ToolCall(id="t1.0", name=SEARCH_TOOLS, arguments={"query": "jira"})
    # The tool search's unavailable hits come back malformed: that one stage cannot be built.
    result = ToolResult(tool_call_id="t1.0", name=SEARCH_TOOLS, content={"tools": [], "unavailable": 5})

    with caplog.at_level(logging.WARNING):
        events = await presenter.result(call, result)

    assert [e.type for e in events] == ["x.pipeshub.tool.result_presented", CITATIONS_EVENT]
    assert events[1].data["refs"] == [{"url": URL, "ref": "ref1"}]
    failed = [r for r in caplog.records if getattr(r, "harness_event", "") == "presentation.failed"]
    assert [r.harness["stage"] for r in failed] == ["unavailable"]  # type: ignore[attr-defined]

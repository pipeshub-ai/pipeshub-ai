"""Shared pieces of a knowledge-search tool result (``ops/results.py``)."""

from __future__ import annotations

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agent_loop_lib.core.types import ToolResult
from app.agents.actions.knowledge_graph.ops.results import (
    compose_result_tail,
    dedupe_append_final_results,
    search_result_summary,
    tool_output,
)
from app.utils.chat_helpers import image_dict_to_part

_IMAGE = {"ref": "ref1", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}


def _result(content: object) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="knowledgegraph__search", content=content)


def _block(vrid: str, index: int | None) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, "content": "x"}


class TestDedupeAppendFinalResults:
    def test_skips_blocks_already_accumulated(self) -> None:
        merged = dedupe_append_final_results([_block("v1", 0)], [_block("v1", 0), _block("v1", 1)])
        assert [(b["virtual_record_id"], b["block_index"]) for b in merged] == [("v1", 0), ("v1", 1)]

    def test_keeps_entries_without_a_block_index(self) -> None:
        assert len(dedupe_append_final_results([_block("v1", None)], [_block("v1", None)])) == 2

    def test_wrongly_typed_accumulated_state_starts_over(self) -> None:
        assert dedupe_append_final_results("oops", [_block("v1", 0)]) == [_block("v1", 0)]  # type: ignore[arg-type]
        assert dedupe_append_final_results([None, "x", _block("v1", 0)], [_block("v1", 0)]) == [_block("v1", 0)]


class TestSearchResultSummary:
    _TEXT = (
        "Top 36 blocks from 21 records (ranked sample — other records may match).\n\n"
        "<record>\nRecord ID: r1\nName: Doc A\n\n"
        "<record>\nRecord ID: r2\nName: Doc B\n\n"
    )

    def test_header_and_record_names(self) -> None:
        summary = search_result_summary({"query": "bug bash"}, _result(self._TEXT))
        assert summary == "Retrieved 36 blocks from 21 records\n- Doc A\n- Doc B"

    def test_a_result_with_images_is_summarised_from_its_text(self) -> None:
        image = image_dict_to_part(_IMAGE)
        assert isinstance(image, ImagePart)
        summary = search_result_summary({}, _result([TextPart(text=self._TEXT), image]))
        assert summary == "Retrieved 36 blocks from 21 records\n- Doc A\n- Doc B"

    def test_the_legacy_tool_result_is_summarised_too(self) -> None:
        """Its own summary looked for a header the tool stopped printing, so it said nothing."""
        content = "Top 1 block from 1 record (ranked sample — other records may match).\n\nName: Doc A\n"
        assert search_result_summary({}, _result(content)) == "Retrieved 1 block from 1 record\n- Doc A"

    def test_no_results_found(self) -> None:
        content = '{"status":"success","message":"No results found","results":[],"result_count":0}'
        assert search_result_summary({}, _result(content)) == "No results found"

    def test_error_envelope(self) -> None:
        content = '{"status":"error","message":"Search error: timeout"}'
        assert search_result_summary({}, _result(content)).startswith("Search failed:")

    def test_no_text_returns_none(self) -> None:
        assert search_result_summary({}, _result(None)) is None
        assert search_result_summary({}, _result([])) is None


class TestComposeResultTail:
    _CANDIDATES = "\n\nCoverage is incomplete (as low as 5%) — call fetch_record."

    def test_navigate_tip_precedes_the_candidate_list(self) -> None:
        """The tip names another tool, so after the candidates it read as the
        way to get more, and the model navigated instead of fetching."""
        tail = compose_result_tail({"v1": {"record_type": "TICKET"}}, self._CANDIDATES)
        assert tail.index("navigate") < tail.index("Coverage is incomplete")
        assert tail.endswith(self._CANDIDATES)

    def test_tip_says_navigate_returns_no_content(self) -> None:
        tail = compose_result_tail({"v1": {"record_type": "CONFLUENCE_PAGE"}}, "")
        assert "not content" in tail
        assert "still a fetch" in tail

    def test_no_tip_for_flat_records(self) -> None:
        assert compose_result_tail({"v1": {"record_type": "FILE"}}, self._CANDIDATES) == self._CANDIDATES

    def test_survives_malformed_record_entries(self) -> None:
        assert compose_result_tail({"v1": None, "v2": "oops", "v3": {}}, "") == ""


class TestToolOutput:
    def test_text_only_without_images(self) -> None:
        assert tool_output("text", [], {"is_multimodal_llm": True}) == "text"

    def test_text_only_for_a_text_model(self) -> None:
        assert tool_output("text", [_IMAGE], {"is_multimodal_llm": False}) == "text"

    def test_images_travel_with_the_text_for_a_multimodal_model(self) -> None:
        state = {"is_multimodal_llm": True}
        output = tool_output("text", [_IMAGE], state)
        assert isinstance(output, list)
        assert output[0] == TextPart(text="text")
        assert len(output) == 2
        assert "pending_tool_images" not in state

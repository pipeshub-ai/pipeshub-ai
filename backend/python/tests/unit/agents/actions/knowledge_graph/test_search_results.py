"""Shared pieces of a knowledge-search tool result (``ops/results.py``)."""

from __future__ import annotations

from app.agent_loop_lib.core.messages import TextPart
from app.agent_loop_lib.core.types import ToolResult
from app.agents.actions.knowledge_graph.ops.results import (
    compose_result_tail,
    dedupe_append_final_results,
    search_result_summary,
    tool_output,
)


def _result(content: object) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="knowledgegraph__search", content=content)


def _block(vrid: str, index: int | None) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, "content": "x"}


class TestDedupeAppendFinalResults:
    def test_skips_blocks_already_accumulated(self) -> None:
        existing = [_block("v1", 0)]
        merged = dedupe_append_final_results(existing, [_block("v1", 0), _block("v1", 1)])
        assert [(b["virtual_record_id"], b["block_index"]) for b in merged] == [("v1", 0), ("v1", 1)]

    def test_keeps_entries_without_a_block_index(self) -> None:
        merged = dedupe_append_final_results([_block("v1", None)], [_block("v1", None)])
        assert len(merged) == 2


class TestSearchResultSummary:
    def test_ranked_header_and_record_names(self) -> None:
        content = (
            "Top 36 blocks from 21 records, most relevant record first "
            "(a ranked sample — other records may match).\n\n"
            "<record>\nRecord ID: r1\nName: Doc A\n\n"
            "<record>\nRecord ID: r2\nName: Doc B\n\n"
        )
        summary = search_result_summary({"query": "bug bash"}, _result(content))
        assert summary == "Retrieved 36 blocks from 21 records\n- Doc A\n- Doc B"

    def test_no_results_found(self) -> None:
        content = '{"status":"success","message":"No results found","results":[],"result_count":0}'
        assert search_result_summary({}, _result(content)) == "No results found"

    def test_error_envelope(self) -> None:
        content = '{"status":"error","message":"Search error: timeout"}'
        assert search_result_summary({}, _result(content)).startswith("Search failed:")

    def test_non_string_content_returns_none(self) -> None:
        assert search_result_summary({}, _result(None)) is None


class TestComposeResultTail:
    _CANDIDATES = "\n\nFetch candidates: ..."

    def test_navigate_tip_precedes_the_candidate_list(self) -> None:
        tail = compose_result_tail({"v1": {"record_type": "TICKET"}}, self._CANDIDATES)
        assert "navigate" in tail
        assert tail.endswith(self._CANDIDATES)

    def test_no_tip_for_flat_records(self) -> None:
        assert compose_result_tail({"v1": {"record_type": "FILE"}}, self._CANDIDATES) == self._CANDIDATES

    def test_survives_malformed_record_entries(self) -> None:
        assert compose_result_tail({"v1": None, "v2": "oops", "v3": {}}, "") == ""


_IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}


class TestToolOutput:
    def test_text_only_without_images(self) -> None:
        assert tool_output("text", [], {"is_multimodal_llm": True}) == "text"

    def test_text_only_for_a_text_model(self) -> None:
        assert tool_output("text", [_IMAGE], {"is_multimodal_llm": False}) == "text"

    def test_images_travel_with_the_text_for_a_multimodal_model(self) -> None:
        output = tool_output("text", [_IMAGE], {"is_multimodal_llm": True})
        assert isinstance(output, list)
        assert output[0] == TextPart(text="text")
        assert len(output) == 2

    def test_images_are_stashed_when_the_transport_cannot_carry_them(self) -> None:
        state = {"is_multimodal_llm": True, "supports_multipart_tool_result": False}
        tool_output("text", [_IMAGE], state)
        assert state["pending_tool_images"] == [_IMAGE]

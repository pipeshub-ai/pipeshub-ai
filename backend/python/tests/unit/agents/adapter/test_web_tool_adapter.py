"""`WebToolAdapter` (`app/agents/agent_loop/web_tool_adapter.py`) — restores
`ToolHandlerRegistry.format_message`'s citation formatting (the `url/
Citation ID:` marker and `CitationRefMapper` registration) for the dynamic
`web_search`/`fetch_url` tools, on top of `PipesHubStructuredToolAdapter`'s
plain success/failure handling."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.web_tool_adapter import (
    WebToolAdapter,
    accumulate_web_records,
    is_web_payload,
)
from app.utils.chat_helpers import CitationRefMapper
from tests.unit.agents.adapter.conftest import make_context


class _WebSearchArgs(BaseModel):
    query: str


def _web_search_tool(payload: dict[str, Any] | str) -> StructuredTool:
    async def _run(**_kwargs: Any) -> Any:
        return payload

    return StructuredTool.from_function(
        name="web_search", description="Search the web", args_schema=_WebSearchArgs, coroutine=_run,
    )


def _fetch_url_tool(payload: dict[str, Any] | str) -> StructuredTool:
    async def _run(**_kwargs: Any) -> Any:
        return payload

    return StructuredTool.from_function(
        name="fetch_url", description="Fetch a URL", args_schema=_WebSearchArgs, coroutine=_run,
    )


def _web_search_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "result_type": "web_search",
        "query": "python 3.12 release",
        "web_results": [
            {
                "title": "Python 3.12 release notes",
                "link": "https://docs.python.org/3.12/whatsnew/3.12.html",
                "snippet": "Python 3.12 was released with several new features.",
            },
        ],
    }
    payload.update(overrides)
    return payload


def _url_content_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "result_type": "url_content",
        "url": "https://example.com/report",
        "blocks": [{"type": "text", "content": "The report says the sky is blue."}],
    }
    payload.update(overrides)
    return payload


class TestIsWebPayload:
    def test_web_search_result_type_is_web_payload(self) -> None:
        assert is_web_payload(_web_search_payload()) is True

    def test_url_content_result_type_is_web_payload(self) -> None:
        assert is_web_payload(_url_content_payload()) is True

    def test_failed_envelope_is_not_web_payload(self) -> None:
        """A failed web call must reach the model as fetch_url_tool.py's/
        web_search_tool.py's own plain error text, not get reformatted as
        if it were a successful "Blocks of content..." envelope."""
        assert is_web_payload({"ok": False, "error": "rate limited"}) is False

    def test_non_dict_payload_is_not_web_payload(self) -> None:
        assert is_web_payload("plain string result") is False
        assert is_web_payload(None) is False

    def test_shape_sniffing_without_result_type(self) -> None:
        assert is_web_payload({"web_results": []}) is True
        assert is_web_payload({"blocks": []}) is True

    def test_unrelated_dynamic_tool_payload_is_not_web_payload(self) -> None:
        assert is_web_payload({"ok": True, "rows": [{"a": 1}]}) is False


class TestAccumulateWebRecords:
    def test_appends_new_records(self) -> None:
        tool_state: dict[str, Any] = {}
        accumulate_web_records(
            tool_state, [{"url": "https://example.com/a", "content": "A content", "title": "A"}],
        )
        assert tool_state["web_records"] == [
            {"url": "https://example.com/a", "content": "A content", "title": "A"},
        ]

    def test_dedupes_by_url_across_calls(self) -> None:
        tool_state: dict[str, Any] = {
            "web_records": [{"url": "https://example.com/a", "content": "first", "title": "A"}],
        }
        accumulate_web_records(
            tool_state, [{"url": "https://example.com/a", "content": "duplicate", "title": "A dup"}],
        )
        assert len(tool_state["web_records"]) == 1
        assert tool_state["web_records"][0]["content"] == "first"

    def test_drops_records_with_no_content(self) -> None:
        """A citation with no content is useless (`normalize_citations_and_
        chunks` skips it at citation time anyway) — don't pad `web_records`
        with entries no citation pass will ever use."""
        tool_state: dict[str, Any] = {}
        accumulate_web_records(tool_state, [{"url": "https://example.com/empty", "content": ""}])
        assert tool_state.get("web_records") == []

    def test_drops_records_with_no_url(self) -> None:
        tool_state: dict[str, Any] = {}
        accumulate_web_records(tool_state, [{"content": "some content"}])
        assert tool_state.get("web_records") == []

    def test_empty_records_list_is_a_no_op(self) -> None:
        tool_state: dict[str, Any] = {}
        accumulate_web_records(tool_state, [])
        assert "web_records" not in tool_state


class TestWebToolAdapterExecute:
    async def test_web_search_success_formats_through_registry_and_populates_sources(self) -> None:
        context = make_context()
        adapter = WebToolAdapter(_web_search_tool(_web_search_payload()), "dynamic", "web_search", context)

        output = await adapter.execute(query="python 3.12 release")

        assert output.success is True
        # The registry-formatted text carries the `url/Citation ID:` marker
        # the model needs in order to cite anything — the raw JSON dump
        # `PipesHubStructuredToolAdapter` would have produced does not.
        assert "url/Citation ID:" in output.data
        assert "Python 3.12 release notes" in output.data
        assert len(output.sources) == 1
        assert output.sources[0].title == "Python 3.12 release notes"

    async def test_web_search_populates_tool_state_web_records(self) -> None:
        context = make_context()
        adapter = WebToolAdapter(_web_search_tool(_web_search_payload()), "dynamic", "web_search", context)

        await adapter.execute(query="python 3.12 release")

        records = context.tool_state["web_records"]
        assert len(records) == 1
        assert records[0]["content"] == "Python 3.12 was released with several new features."

    async def test_web_search_registers_citation_ref_mapper_entry(self) -> None:
        """`display_url_for_llm` (called from inside `format_message`) is
        what registers each block's URL into the shared `CitationRefMapper`
        — the tiny `refN` ids the model actually emits in its answer."""
        context = make_context()
        ref_mapper = CitationRefMapper()
        context.tool_state["citation_ref_mapper"] = ref_mapper
        adapter = WebToolAdapter(_web_search_tool(_web_search_payload()), "dynamic", "web_search", context)

        await adapter.execute(query="python 3.12 release")

        assert len(ref_mapper.ref_to_url) == 1

    async def test_fetch_url_success_formats_through_registry(self) -> None:
        context = make_context(config_service=None)
        adapter = WebToolAdapter(_fetch_url_tool(_url_content_payload()), "dynamic", "fetch_url", context)

        output = await adapter.execute(url="https://example.com/report")

        assert output.success is True
        assert "url/Citation ID:" in output.data
        assert "The report says the sky is blue." in output.data
        assert len(output.sources) == 1
        assert output.sources[0].url is not None

    async def test_fetch_url_populates_tool_state_web_records(self) -> None:
        context = make_context(config_service=None)
        adapter = WebToolAdapter(_fetch_url_tool(_url_content_payload()), "dynamic", "fetch_url", context)

        await adapter.execute(url="https://example.com/report")

        records = context.tool_state["web_records"]
        assert len(records) == 1
        assert records[0]["content"] == "The report says the sky is blue."

    async def test_failed_tool_call_is_not_reformatted(self) -> None:
        context = make_context()
        adapter = WebToolAdapter(_fetch_url_tool({"ok": False, "error": "not found"}), "dynamic", "fetch_url", context)

        output = await adapter.execute(url="https://example.com/missing")

        assert output.success is False
        assert context.tool_state["web_records"] == []

    async def test_underlying_exception_still_wraps_as_failure(self) -> None:
        async def _boom(**_kwargs: Any) -> Any:
            raise ValueError("network error")

        structured_tool = StructuredTool.from_function(
            name="fetch_url", description="Fetch a URL", args_schema=_WebSearchArgs, coroutine=_boom,
        )
        context = make_context()
        adapter = WebToolAdapter(structured_tool, "dynamic", "fetch_url", context)

        output = await adapter.execute(url="https://example.com/x")

        assert output.success is False
        assert "network error" in output.error

    async def test_non_web_shaped_success_payload_passes_through_unchanged(self) -> None:
        """Some other dynamic tool wrapped by `WebToolAdapter` by mistake
        (or a future web tool with a differently-shaped success payload)
        must not be mangled — only a recognized web envelope gets
        reformatted."""
        context = make_context()
        structured_tool = _web_search_tool({"ok": True, "rows": [{"a": 1}]})
        adapter = WebToolAdapter(structured_tool, "dynamic", "web_search", context)

        output = await adapter.execute(query="x")

        assert output.success is True
        assert context.tool_state["web_records"] == []
        assert output.sources == []

    async def test_no_web_results_still_formats_header_with_empty_sources(self) -> None:
        context = make_context()
        adapter = WebToolAdapter(
            _web_search_tool(_web_search_payload(web_results=[])), "dynamic", "web_search", context,
        )

        output = await adapter.execute(query="obscure query with no hits")

        assert output.success is True
        assert "url/Citation ID:" not in output.data
        assert output.sources == []
        assert context.tool_state.get("web_records") == []

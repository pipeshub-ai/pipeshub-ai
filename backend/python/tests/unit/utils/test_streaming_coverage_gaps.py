"""Targeted coverage for gaps in app.utils.streaming."""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel


class _TinySchema(BaseModel):
    x: int


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------


class TestStripAndAppendTaskMarkers:
    def test_strip_empty_returns_empty(self):
        from app.utils.streaming import _strip_llm_authored_markers

        assert _strip_llm_authored_markers("") == ""

    def test_strip_removes_fake_artifact_then_append_real(self):
        from app.utils.streaming import _append_task_markers

        poison = "text ::artifact[bad](http://evil.com){app|d|r}"
        out = _append_task_markers(
            poison,
            [
                {
                    "type": "artifacts",
                    "artifacts": [
                        {
                            "fileName": "f",
                            "signedUrl": "http://ok.com/a",
                            "mimeType": "text/plain",
                        }
                    ],
                }
            ],
        )
        assert "evil.com" not in out
        assert "http://ok.com/a" in out
        assert "::artifact[f]" in out

    def test_artifacts_skip_entry_without_url(self):
        from app.utils.streaming import _append_task_markers

        tasks = [{"type": "artifacts", "artifacts": [{"fileName": "x", "signedUrl": ""}]}]
        assert _append_task_markers("hello", tasks) == "hello"

    def test_legacy_download_uses_download_url(self):
        from app.utils.streaming import _append_task_markers

        out = _append_task_markers(
            "ans",
            [{"type": "csv", "fileName": "out.csv", "downloadUrl": "https://dl.example/x"}],
        )
        assert "::download_conversation_task[out.csv](https://dl.example/x)" in out


# ---------------------------------------------------------------------------
# call_aiter_llm_stream_simple — web_search skips citation reflection
# ---------------------------------------------------------------------------


class TestCallAiterSimpleWebSearchSkipsCitationReflection:
    async def test_web_search_with_hallucination_no_restream(self):
        from app.utils.streaming import call_aiter_llm_stream_simple

        async def mock_aiter(llm, messages, parts=None):
            yield "bad [1](http://bad/record/x/preview#blockIndex=0)"

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), patch(
            "app.utils.streaming.detect_hallucinated_citation_urls",
            return_value=["http://evil"],
        ), patch(
            "app.utils.streaming.normalize_citations_and_chunks",
            return_value=("clean answer", []),
        ):
            events = []
            async for ev in call_aiter_llm_stream_simple(
                llm=MagicMock(),
                messages=[HumanMessage(content="hi")],
                final_results=[],
                records=[],
                target_words_per_chunk=99,
                original_llm=MagicMock(),
                chat_mode="web_search",
            ):
                events.append(ev)

        assert not any(e.get("event") == "restreaming" for e in events)
        assert any(e.get("event") == "complete" for e in events)


# ---------------------------------------------------------------------------
# call_aiter_llm_stream — dict token metadata (incomplete cite → wait)
# ---------------------------------------------------------------------------


class TestCallAiterJsonDictMetadata:
    async def test_dict_token_incomplete_citation_emits_metadata_then_completes(self):
        from app.utils.streaming import call_aiter_llm_stream

        good_answer = "Hi [1](http://ok/preview#blockIndex=0)"
        final_snapshot = {
            "answer": good_answer,
            "reason": "r",
            "confidence": "High",
            "answerMatchType": "Derived From Blocks",
            "referenceData": [],
        }

        async def mock_tokens(llm, messages, parts=None):
            yield {"answer": "["}
            yield final_snapshot

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_tokens), patch(
            "app.utils.streaming.detect_hallucinated_citation_urls", return_value=[]
        ), patch(
            "app.utils.streaming.normalize_citations_and_chunks",
            side_effect=lambda text, *a, **k: (text, []),
        ):
            events = []
            async for ev in call_aiter_llm_stream(
                MagicMock(),
                [],
                [],
                [],
                target_words_per_chunk=10,
                original_llm=MagicMock(),
            ):
                events.append(ev)

        assert any(e.get("event") == "metadata" for e in events)
        assert any(e.get("event") == "complete" for e in events)


# ---------------------------------------------------------------------------
# call_aiter_function branches
# ---------------------------------------------------------------------------


class TestCallAiterFunctionDispatchesSimple:
    async def test_simple_mode_dispatches_to_call_aiter_llm_stream_simple(self):
        from app.utils.streaming import call_aiter_function

        seen = []

        async def fake_simple(**kwargs):
            seen.append(kwargs.get("messages"))
            if False:
                yield {}

        async def fake_json(**kwargs):
            seen.append("json-wrong-branch")
            if False:
                yield {}

        with patch(
            "app.utils.streaming.call_aiter_llm_stream_simple", side_effect=fake_simple
        ) as mock_s, patch("app.utils.streaming.call_aiter_llm_stream", side_effect=fake_json):
            msgs = [{"role": "user", "content": "q"}]
            async for _ in call_aiter_function(
                MagicMock(),
                msgs,
                [],
                mode="simple",
            ):
                pass

        mock_s.assert_called_once()
        assert seen and seen[0] is msgs


# ---------------------------------------------------------------------------
# invoke_with_structured_output_and_reflection
# ---------------------------------------------------------------------------


class TestInvokeStructuredOutputMinimal:
    async def test_dict_with_content_json_success(self):
        from app.utils.streaming import invoke_with_structured_output_and_reflection

        wrapped = MagicMock()
        wrapped.ainvoke = AsyncMock(return_value={"content": '{"x": 42}'})
        fake_llm = MagicMock()

        with patch(
            "app.utils.streaming._apply_structured_output", return_value=wrapped
        ):
            out = await invoke_with_structured_output_and_reflection(
                fake_llm,
                [],
                _TinySchema,
                max_retries=0,
            )
        assert out is not None and out.x == 42

    async def test_ainvoke_fails_returns_none(self):
        from app.utils.streaming import invoke_with_structured_output_and_reflection

        wrapped = MagicMock()
        wrapped.ainvoke = AsyncMock(side_effect=RuntimeError("api down"))

        with patch(
            "app.utils.streaming._apply_structured_output", return_value=wrapped
        ):
            out = await invoke_with_structured_output_and_reflection(
                MagicMock(),
                [],
                _TinySchema,
                max_retries=1,
            )
        assert out is None

    async def test_plain_dict_validate_success(self):
        from app.utils.streaming import invoke_with_structured_output_and_reflection

        wrapped = MagicMock()
        wrapped.ainvoke = AsyncMock(return_value={"x": 7})

        with patch(
            "app.utils.streaming._apply_structured_output", return_value=wrapped
        ):
            out = await invoke_with_structured_output_and_reflection(
                MagicMock(),
                [],
                _TinySchema,
                max_retries=0,
            )
        assert out is not None and out.x == 7

    async def test_plain_pydantic_model_response_branch(self):
        from app.utils.streaming import invoke_with_structured_output_and_reflection

        wrapped = MagicMock()
        wrapped.ainvoke = AsyncMock(return_value=_TinySchema(x=88))

        with patch(
            "app.utils.streaming._apply_structured_output", return_value=wrapped
        ):
            out = await invoke_with_structured_output_and_reflection(
                MagicMock(),
                [],
                _TinySchema,
                max_retries=0,
            )
        assert out is not None and out.x == 88

    async def test_initial_parse_failure_reflection_fixes_via_message_content(self):
        from app.utils.streaming import invoke_with_structured_output_and_reflection

        wrapped = MagicMock()
        wrapped.ainvoke = AsyncMock(
            side_effect=[
                AIMessage(content="not-json"),
                AIMessage(content='{"x": 3}'),
            ]
        )

        with patch(
            "app.utils.streaming._apply_structured_output", return_value=wrapped
        ):
            out = await invoke_with_structured_output_and_reflection(
                MagicMock(),
                [],
                _TinySchema,
                max_retries=2,
            )
        assert out is not None and out.x == 3


class TestInvokeRowDescriptionsReflection:
    async def test_count_match_returns_immediately(self):
        from app.modules.parsers.excel.prompt_template import RowDescriptions
        from app.utils.streaming import invoke_with_row_descriptions_and_reflection

        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new=AsyncMock(return_value=RowDescriptions(descriptions=["a", "b"])),
        ):
            row = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [], expected_count=2, max_retries=1
            )
        assert row is not None and len(row.descriptions) == 2

    async def test_count_mismatch_reflection_returns_corrected(self):
        from app.modules.parsers.excel.prompt_template import RowDescriptions
        from app.utils.streaming import invoke_with_row_descriptions_and_reflection

        fixed = RowDescriptions(descriptions=["one", "two", "three"])

        async def refl_side_effect(*args, **kwargs):
            refl_side_effect.call_count = getattr(refl_side_effect, "call_count", 0) + 1
            if refl_side_effect.call_count == 1:
                return RowDescriptions(descriptions=["only-one"])
            return fixed

        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new=AsyncMock(side_effect=refl_side_effect),
        ):
            row = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [], expected_count=3, max_retries=1
            )
        assert row is not None and len(row.descriptions) == 3


# ---------------------------------------------------------------------------
# execute_tool_calls — web record, deferred tool, retrieval hit, ContentHandler instr.
# ---------------------------------------------------------------------------


class TestExecuteToolCallsBranches:
    async def test_web_extract_appends_web_records_and_tool_error_yield(self):
        from app.utils.streaming import execute_tool_calls

        ok_tool = MagicMock()
        ok_tool.name = "web_tool"
        ok_tool.arun = AsyncMock(
            return_value=json.dumps(
                {
                    "ok": True,
                    "web_results": [{"link": "http://ex", "snippet": "sn", "title": "t"}],
                    "query": "qq",
                }
            )
        )

        bad_tool = MagicMock()
        bad_tool.name = "bad_tool"
        bad_tool.arun = AsyncMock(return_value={"ok": False, "error": "boom"})

        ai = MagicMock()
        ai.content = ""
        ai.tool_calls = [
            {"name": "web_tool", "args": {}, "id": "c1"},
            {"name": "bad_tool", "args": {}, "id": "c2"},
        ]

        ai_done = MagicMock()
        ai_done.content = ""
        ai_done.tool_calls = []

        hop = {"n": 0}

        async def mock_stream(*a, **kw):
            if hop["n"] == 0:
                hop["n"] += 1
                yield {"event": "tool_calls", "data": {"ai": ai}}
                return
            yield {"event": "tool_calls", "data": {"ai": ai_done}}

        async def noop_search(*args, **kwargs):
            raise AssertionError("retrieval should not run")

        retrieval = AsyncMock(side_effect=noop_search)

        with patch(
            "app.utils.streaming.call_aiter_llm_stream", side_effect=mock_stream
        ), patch("app.utils.streaming.bind_tools_for_llm", return_value=MagicMock()), patch(
            "app.utils.streaming.count_tokens", return_value=(0, 0)
        ), patch(
            "app.utils.streaming.supports_human_message_after_tool", return_value=False
        ):
            events = []
            async for ev in execute_tool_calls(
                MagicMock(),
                [],
                tools=[ok_tool, bad_tool],
                tool_runtime_kwargs={},
                final_results=[],
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                all_queries=["q"],
                retrieval_service=retrieval,
                user_id="u",
                org_id="o",
                context_length=8000,
                mode="json",
            ):
                events.append(ev)

        errs = [e for e in events if e.get("event") == "tool_error"]
        assert len(errs) == 1
        complete = next(e for e in events if e.get("event") == "tool_execution_complete")
        ws = complete["data"].get("web_records", [])
        assert any(rec.get("source_type") == "web" for rec in ws)

    async def test_deferred_tool_attached_after_trigger(self):
        from app.utils.streaming import execute_tool_calls

        trig = MagicMock()
        trig.name = "unlock"
        trig.arun = AsyncMock(return_value={"ok": True, "records": []})
        defer = MagicMock()
        defer.name = "bonus"
        defer.arun = AsyncMock(return_value={"ok": True, "records": []})

        tools = [trig]

        ai1 = MagicMock()
        ai1.content = ""
        ai1.tool_calls = [{"name": "unlock", "args": {}, "id": "1"}]

        ai2 = MagicMock()
        ai2.content = ""
        ai2.tool_calls = []

        step = {"i": 0}

        async def mock_stream(*a, **kw):
            if step["i"] == 0:
                step["i"] += 1
                yield {"event": "tool_calls", "data": {"ai": ai1}}
            else:
                yield {"event": "tool_calls", "data": {"ai": ai2}}

        with patch(
            "app.utils.streaming.call_aiter_llm_stream", side_effect=mock_stream
        ), patch("app.utils.streaming.bind_tools_for_llm", return_value=MagicMock()), patch(
            "app.utils.streaming.count_tokens", return_value=(0, 0)
        ), patch(
            "app.utils.streaming.supports_human_message_after_tool", return_value=False
        ):
            events = []
            async for ev in execute_tool_calls(
                MagicMock(),
                [HumanMessage(content="hi")],
                tools=tools,
                tool_runtime_kwargs={},
                final_results=[],
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                all_queries=["q"],
                retrieval_service=AsyncMock(),
                user_id="u",
                org_id="o",
                context_length=8000,
                mode="json",
                defer_tool_until_called_name="unlock",
                deferred_tool=defer,
            ):
                events.append(ev)

        assert any(t.name == "bonus" for t in tools)

    async def test_token_overflow_with_search_results_rebuilds_message_contents(self):
        from app.utils.streaming import execute_tool_calls

        rec = {"virtual_record_id": "vr9", "content": "x"}
        fetch_tool = MagicMock()
        fetch_tool.name = "grab"
        fetch_tool.arun = AsyncMock(return_value={"ok": True, "records": [rec]})

        ai_tc = MagicMock()
        ai_tc.content = ""
        ai_tc.tool_calls = [{"name": "grab", "args": {}, "id": "z1"}]
        ai_done = MagicMock()
        ai_done.content = ""
        ai_done.tool_calls = []

        hops = {"n": 0}

        async def mock_stream(*a, **kw):
            if hops["n"] == 0:
                hops["n"] += 1
                yield {"event": "tool_calls", "data": {"ai": ai_tc}}
                return
            yield {"event": "tool_calls", "data": {"ai": ai_done}}

        def mock_record_to_content(record, ref_mapper=None):
            return ([{"type": "text", "text": str(record)}], ref_mapper)

        retrieval_payload = {"searchResults": [{"id": 1}], "status_code": 200}

        calls_ct = {"n": 0}

        def counting_tokens(*_a, **_k):
            calls_ct["n"] += 1
            if calls_ct["n"] <= 1:
                return (10**9, 10**9)
            return (0, 0)

        with patch(
            "app.utils.streaming.call_aiter_llm_stream", side_effect=mock_stream
        ), patch("app.utils.streaming.bind_tools_for_llm", return_value=MagicMock()), patch(
            "app.utils.streaming.count_tokens", side_effect=counting_tokens
        ), patch(
            "app.utils.streaming.record_to_message_content", side_effect=mock_record_to_content
        ), patch(
            "app.utils.streaming.supports_human_message_after_tool", return_value=False
        ), patch(
            "app.utils.streaming.get_flattened_results",
            new=AsyncMock(return_value=[{"virtual_record_id": "vr9", "block_index": 0}]),
        ), patch(
            "app.utils.streaming.build_message_content_array",
            return_value=([[{"type": "text", "text": "fallback"}]], None),
        ):
            rs = MagicMock()
            rs.search_with_filters = AsyncMock(return_value=retrieval_payload)
            async for _ in execute_tool_calls(
                MagicMock(),
                [HumanMessage(content="base")],
                tools=[fetch_tool],
                tool_runtime_kwargs={},
                final_results=[],
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                all_queries=["q"],
                retrieval_service=rs,
                user_id="u",
                org_id="o",
                context_length=20000,
                mode="json",
            ):
                pass

        rs.search_with_filters.assert_awaited()

    async def test_content_handler_appends_instructions_to_last_tool_message(self):
        from app.utils.tool_handlers import ContentHandler
        from app.utils.streaming import execute_tool_calls

        sql_tool = MagicMock()
        sql_tool.name = "sqlrun"
        sql_tool.arun = AsyncMock(return_value={"ok": True, "content": "rows", "result_type": "content"})

        ai_tc = MagicMock()
        ai_tc.content = ""
        ai_tc.tool_calls = [{"name": "sqlrun", "args": {}, "id": "s1"}]
        ai_done = MagicMock()
        ai_done.content = ""
        ai_done.tool_calls = []

        hops = {"n": 0}

        async def mock_stream(*a, **kw):
            if hops["n"] == 0:
                hops["n"] += 1
                yield {"event": "tool_calls", "data": {"ai": ai_tc}}
                return
            yield {"event": "tool_calls", "data": {"ai": ai_done}}

        async def _stub_format_message(_self, *_a, **_k):
            return [{"type": "text", "text": "formatted"}]

        with patch(
            "app.utils.streaming.call_aiter_llm_stream", side_effect=mock_stream
        ), patch("app.utils.streaming.bind_tools_for_llm", return_value=MagicMock()), patch(
            "app.utils.streaming.count_tokens", return_value=(0, 0)
        ), patch(
            "app.utils.streaming.supports_human_message_after_tool", return_value=True
        ), patch.object(ContentHandler, "format_message", new=_stub_format_message):
            msgs: list = [HumanMessage(content="prompt")]
            async for _ in execute_tool_calls(
                MagicMock(),
                msgs,
                tools=[sql_tool],
                tool_runtime_kwargs={"has_sql_connector": True},
                final_results=[],
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                all_queries=["q"],
                retrieval_service=AsyncMock(),
                user_id="u",
                org_id="o",
                context_length=8000,
                mode="json",
            ):
                pass

        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert tool_msgs, "expected at least one ToolMessage"
        last_tool_content = tool_msgs[-1].content
        if isinstance(last_tool_content, list):
            tool_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in last_tool_content
            )
        else:
            tool_text = str(last_tool_content)
        instructions = ContentHandler.build_tool_instructions(True)
        assert instructions in tool_text


# ---------------------------------------------------------------------------
# Opik optional init — reload module under patched env / successful configure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["success", "bad_import"])
def test_opik_module_reload_config_branches(monkeypatch, scenario):
    """Exercise module-level Opik init via importlib.reload (lines 61–70)."""

    import types

    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    monkeypatch.delenv("OPIK_WORKSPACE", raising=False)
    importlib.reload(importlib.import_module("app.utils.streaming"))

    monkeypatch.setenv("OPIK_API_KEY", "k")
    monkeypatch.setenv("OPIK_WORKSPACE", "ws")

    tracer_inst = MagicMock()
    opik_mod = types.ModuleType("opik")
    integrations_mod = types.ModuleType("opik.integrations")
    langchain_mod = types.ModuleType("opik.integrations.langchain")

    if scenario == "success":
        opik_mod.configure = MagicMock(return_value=None)
        langchain_mod.OpikTracer = MagicMock(return_value=tracer_inst)
    else:

        def _boom(**_kwargs):
            raise RuntimeError("no opik")

        opik_mod.configure = MagicMock(side_effect=_boom)

    integrations_mod.langchain = langchain_mod
    opik_mod.integrations = integrations_mod

    mods = {
        "opik": opik_mod,
        "opik.integrations": integrations_mod,
        "opik.integrations.langchain": langchain_mod,
    }

    try:
        with patch.dict(sys.modules, mods, clear=False):
            mod = importlib.reload(importlib.import_module("app.utils.streaming"))
            if scenario == "success":
                assert mod.opik_tracer is tracer_inst
            else:
                assert mod.opik_tracer is None
    finally:
        monkeypatch.delenv("OPIK_API_KEY", raising=False)
        monkeypatch.delenv("OPIK_WORKSPACE", raising=False)
        importlib.reload(importlib.import_module("app.utils.streaming"))


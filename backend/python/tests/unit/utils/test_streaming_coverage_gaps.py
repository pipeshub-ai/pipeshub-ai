"""Targeted coverage for gaps in app.utils.streaming."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
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


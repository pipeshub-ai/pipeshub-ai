"""Unit tests for get_llm_invoke_config callback merging."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.utils.opik import get_llm_invoke_config
from app.utils.streaming import invoke_with_structured_output_and_reflection


def test_get_llm_invoke_config_merges_model_and_opik() -> None:
    usage_cb = MagicMock(name="usage")
    opik_cb = MagicMock(name="opik")
    llm = MagicMock()
    llm.callbacks = [usage_cb]
    llm.bound = None
    llm.bound_llm = None

    with patch("app.utils.opik._opik_tracer", opik_cb):
        config = get_llm_invoke_config(llm)

    assert config["callbacks"] == [usage_cb, opik_cb]


def test_get_llm_invoke_config_does_not_duplicate_opik() -> None:
    opik_cb = MagicMock(name="opik")
    llm = MagicMock()
    llm.callbacks = [opik_cb]
    llm.bound = None
    llm.bound_llm = None

    with patch("app.utils.opik._opik_tracer", opik_cb):
        config = get_llm_invoke_config(llm)

    assert config["callbacks"] == [opik_cb]


def test_get_llm_invoke_config_reads_bound_callbacks() -> None:
    usage_cb = MagicMock(name="usage")
    bound = MagicMock()
    bound.callbacks = [usage_cb]
    llm = MagicMock()
    llm.callbacks = None
    llm.bound = bound
    llm.bound_llm = None

    with patch("app.utils.opik._opik_tracer", None):
        config = get_llm_invoke_config(llm)

    assert config["callbacks"] == [usage_cb]


def test_structured_invoke_passes_merged_config() -> None:
    """Regression: structured-output path must keep UsageCallbackHandler in config."""

    class Schema(BaseModel):
        answer: str

    usage_cb = MagicMock(name="usage")
    opik_cb = MagicMock(name="opik")
    mock_llm = MagicMock(spec=ChatOpenAI)
    mock_llm.callbacks = [usage_cb]
    mock_llm.bound = None
    mock_llm.bound_llm = None
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value={"answer": "ok"})
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)

    with patch("app.utils.opik._opik_tracer", opik_cb):
        result = asyncio.run(
            invoke_with_structured_output_and_reflection(
                mock_llm, [HumanMessage(content="q")], Schema
            )
        )

    assert result is not None
    assert result.answer == "ok"
    kwargs = mock_structured.ainvoke.await_args.kwargs
    assert "config" in kwargs
    cbs = kwargs["config"]["callbacks"]
    assert usage_cb in cbs
    assert opik_cb in cbs


def test_a_self_hosted_opik_without_an_api_key_is_traced(monkeypatch) -> None:
    import importlib
    import sys
    import types

    import app.utils.opik as opik_module

    tracer_cls = MagicMock(name="OpikTracer")
    fake = types.ModuleType("opik.integrations.langchain")
    fake.OpikTracer = tracer_cls
    monkeypatch.setitem(sys.modules, "opik.integrations.langchain", fake)
    monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://opik.internal:5173/api")
    monkeypatch.setenv("OPIK_API_KEY", "")
    monkeypatch.setenv("OPIK_WORKSPACE", "")
    try:
        importlib.reload(opik_module)
        assert opik_module.get_opik_tracer() is tracer_cls.return_value
    finally:
        monkeypatch.undo()
        importlib.reload(opik_module)
    assert opik_module.get_opik_tracer() is None

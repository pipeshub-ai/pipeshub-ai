"""A truncated VLM OCR page costs at most two paid calls.

Before, a page whose answer was cut off at the output cap was sent three times (the page's own retry loop, which treated a
truncation like any failure), each send paid. A cut-off page comes back cut off again, so it now gets one more send and no more.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy, VlmOutputTruncated
from app.services.llm_gateway.gateway import LLMGateway


class _PagesModel:
    """A vision model that answers the calls for page 1 from a script of response_metadata dicts and counts the calls it was
    paid for. Page 2 (a clean page, so the document as a whole can be read) is always answered normally."""

    model_name = "vlm-test"

    def __init__(self, *script: dict[str, Any]) -> None:
        self.script = list(script)
        self.calls = 0  # calls for page 1
        self.other_calls = 0

    async def ainvoke(self, messages: Any, **_: Any) -> Any:  # noqa: ANN401
        if "BBBB" in str(messages):
            self.other_calls += 1
            return SimpleNamespace(content="clean page", response_metadata={"finish_reason": "stop"}, usage_metadata=None)
        self.calls += 1
        metadata = self.script.pop(0) if self.script else {"finish_reason": "stop"}
        return SimpleNamespace(content=f"page text {self.calls}", response_metadata=metadata, usage_metadata=None)


def _strategy(model: _PagesModel) -> VLMOCRStrategy:
    strategy = VLMOCRStrategy(logging.getLogger("t"), MagicMock())
    strategy.llm = model  # type: ignore[assignment]
    strategy.doc = MagicMock()
    strategy.doc.pages = [MagicMock(page_number=1, width=10, height=20), MagicMock(page_number=2, width=10, height=20)]
    strategy._page_images = {1: "data:image/png;base64,AAAA", 2: "data:image/png;base64,BBBB"}
    return strategy


async def _run(strategy: VLMOCRStrategy) -> dict[str, Any]:
    gateway = LLMGateway(2)
    with patch.object(strategy, "_preload_page_images", new_callable=AsyncMock), \
            patch("app.modules.parsers.pdf.vlm_ocr_strategy.get_llm_gateway", return_value=gateway):
        return await strategy._preprocess_document()


@pytest.mark.parametrize(
    "cut",
    [{"finish_reason": "length"}, {"stop_reason": "max_tokens"}, {"finish_reason": "MAX_TOKENS"},
     {"stopReason": "max_tokens"}, {"done_reason": "length"}],
    ids=["openai", "anthropic", "gemini", "bedrock", "ollama"],
)
async def test_truncated_page_costs_at_most_two_calls(cut: dict[str, Any]) -> None:
    model = _PagesModel(cut, cut, cut, cut)
    result = await _run(_strategy(model))
    assert model.calls == 2, f"a page cut off every time was sent {model.calls} times"
    assert model.other_calls == 1
    assert result["pages"][0]["markdown"] == "", "the page that stays cut off is indexed empty, as an unreadable one is"


async def test_a_page_cut_off_once_is_read_on_the_second_send() -> None:
    model = _PagesModel({"finish_reason": "length"}, {"finish_reason": "stop"})
    result = await _run(_strategy(model))
    assert model.calls == 2
    assert result["pages"][0]["markdown"] == "page text 2"


async def test_other_page_failures_keep_their_three_attempts() -> None:
    """Only a truncation is limited: a page that failed some other way still gets the allowance it had."""
    strategy = _strategy(_PagesModel())
    calls = 0

    async def failing(page: object, page_number: int | None = None) -> dict[str, Any]:
        nonlocal calls
        if page_number == 2:
            return {"page_number": 2, "markdown": "clean page", "width": 10, "height": 20}
        calls += 1
        raise ValueError("unreadable")

    with patch.object(strategy, "_preload_page_images", new_callable=AsyncMock), \
            patch.object(strategy, "process_page", side_effect=failing):
        await strategy._preprocess_document()
    assert calls == strategy.MAX_RETRY_ATTEMPTS + 1


async def test_the_truncation_is_its_own_error_type() -> None:
    model = _PagesModel({"finish_reason": "length"})
    strategy = _strategy(model)
    with patch("app.modules.parsers.pdf.vlm_ocr_strategy.get_llm_gateway", return_value=LLMGateway(1)), \
            pytest.raises(VlmOutputTruncated, match="page 1"):
        await strategy._call_llm_for_markdown("data:image/png;base64,AAAA", 1)


async def test_a_page_cut_off_once_and_then_failing_otherwise_keeps_its_two_sends() -> None:
    """The reduced allowance is the page's, not the last error's: a cut-off page whose second send fails another way does not
    get the full retry allowance back."""
    class Flaky(_PagesModel):
        async def ainvoke(self, messages: Any, **kw: Any) -> Any:  # noqa: ANN401
            if "BBBB" in str(messages):
                return await super().ainvoke(messages, **kw)
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="cut", response_metadata={"finish_reason": "length"}, usage_metadata=None)
            raise ValueError("unreadable")

    model = Flaky()
    await _run(_strategy(model))
    assert model.calls == 2

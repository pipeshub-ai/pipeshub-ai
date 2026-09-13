"""The structured-output helper returns None on an outage unless its caller asks for the error."""

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from app.services.llm_gateway.gateway import ProviderUnavailableError
from app.utils import streaming
from app.utils.llm import LLMUnavailableError


class _Answer(BaseModel):
    value: str


async def _down(*_args: object, **_kwargs: object) -> None:
    raise ProviderUnavailableError("provider refused the connection", provider="p")


@pytest.mark.asyncio
async def test_an_outage_is_none_for_callers_that_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming, "_ainvoke_throttled", _down)
    assert await streaming.invoke_with_structured_output_and_reflection(MagicMock(), [], _Answer) is None


@pytest.mark.asyncio
async def test_an_outage_is_raised_for_callers_that_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming, "_ainvoke_throttled", _down)
    with pytest.raises(LLMUnavailableError):
        await streaming.invoke_with_structured_output_and_reflection(MagicMock(), [], _Answer, raise_unavailable=True)


@pytest.mark.asyncio
async def test_throttled_calls_go_through_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Gateway:
        async def invoke(self, runnable: object, messages: object, *, provider: str) -> str:
            seen.append(provider)
            return "answer"

    monkeypatch.setattr(streaming, "get_llm_gateway", lambda: _Gateway())
    assert await streaming._ainvoke_throttled(MagicMock(), [], provider="openai:gpt") == "answer"
    assert seen == ["openai:gpt"]

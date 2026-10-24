"""The structured-output helper returns None on an outage unless its caller asks for the error."""

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from app.agents.agent_loop.prompt_cache import AnthropicCacheStrategy
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
        async def invoke(self, runnable: object, messages: object, *, provider: str, **_kwargs: object) -> str:
            seen.append(provider)
            return "answer"

    monkeypatch.setattr(streaming, "get_llm_gateway", lambda: _Gateway())
    assert await streaming._ainvoke_throttled(MagicMock(), [], provider="openai:gpt") == "answer"
    assert seen == ["openai:gpt"]


class _BadRequest(Exception):
    status_code = 400


@pytest.mark.asyncio
async def test_a_refused_cache_falls_back_and_a_later_outage_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def _cache_refused_then_down(*_args: object, call_kwargs: dict[str, object] | None = None, **_kw: object) -> None:
        calls.append(dict(call_kwargs or {}))
        if len(calls) == 1:
            raise _BadRequest("Error code: 400 - cache_control: Extra inputs are not permitted")
        raise ProviderUnavailableError("provider refused the connection", provider="p")

    rejected: list[frozenset[str]] = []
    monkeypatch.setattr(streaming, "_ainvoke_throttled", _cache_refused_then_down)
    with pytest.raises(LLMUnavailableError):
        await streaming.invoke_with_structured_output_and_reflection(
            MagicMock(), [], _Answer,
            prompt_cache=AnthropicCacheStrategy({"type": "ephemeral"}, top_level=True),
            on_cache_rejected=rejected.append,
            raise_unavailable=True,
        )
    assert rejected == [frozenset({"cache_control"})]
    # The retry after the refusal goes out without the cache parameters.
    assert calls == [{"cache_control": {"type": "ephemeral"}}, {}]

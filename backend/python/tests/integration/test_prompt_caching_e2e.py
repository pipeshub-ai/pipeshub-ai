"""Prompt caching E2E: drives `LangChainTransport` — the path that
carries all real PipesHub chat/agent-loop traffic (see
`app.agents.agent_loop.langchain_transport`'s module docstring) — across
multiple simulated turns with a scripted LangChain-shaped fake model. No
network; the fakes stand in for a real Anthropic/OpenAI SDK response the
same way `tests/unit/agents/adapter/test_langchain_transport.py` does,
but this file exercises the FULL multi-turn arc the plan's own "Tests ->
E2E" section calls for rather than one call in isolation:

- Multi-turn Anthropic run: `cache_read_input_tokens > 0` from turn 2.
- Multi-turn OpenAI (GPT-5.6) run: `cached_tokens > 0` from turn 2.
- Flag toggled off mid-session: subsequent requests carry no directives.
- Minimal-config agent (unrecognized/legacy model, under the floor): no
  cache kwarg sent, decision reason logged, and the run still succeeds.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app.agent_loop_lib.core.messages import UserMessage
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.llm.prompt_cache.config import CacheConfig


def _usage(*, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_token_details": {"cache_read": cache_read, "cache_creation": cache_write},
    }


class _ScriptedModel:
    """A LangChain `BaseChatModel` stand-in whose class name drives
    `detect_langchain_provider`/`resolve_cache_provider` — set via
    `_named` below — and whose `ainvoke` returns the next canned
    response, recording the invoke kwargs it received each turn so
    assertions can check exactly what was (or wasn't) sent."""

    def __init__(self, responses: list[AIMessage], **extra_attrs: Any) -> None:
        self._responses = list(responses)
        self._turn = 0
        self.ainvoke_kwargs_by_turn: list[dict[str, Any]] = []
        for name, value in extra_attrs.items():
            setattr(self, name, value)

    def bind_tools(self, tools: list[Any]) -> "_ScriptedModel":
        return self

    async def ainvoke(self, messages: list, config: Any = None, **kwargs: Any) -> AIMessage:
        self.ainvoke_kwargs_by_turn.append(kwargs)
        response = self._responses[min(self._turn, len(self._responses) - 1)]
        self._turn += 1
        return response


def _named(cls: type, class_name: str) -> type:
    """Renames *cls* (as `type(instance).__name__`, what the provider
    detectors actually read) to a real LangChain class name so
    `detect_langchain_provider` resolves it to a real provider instead
    of falling back to the lowercased fake class name."""
    return type(class_name, (cls,), {})


_FakeChatAnthropic = _named(_ScriptedModel, "ChatAnthropic")
_FakeChatOpenAI = _named(_ScriptedModel, "ChatOpenAI")


async def _run_turns(transport: LangChainTransport, turn_count: int) -> None:
    for i in range(turn_count):
        await transport.complete([UserMessage(content=f"turn {i + 1}")])


class TestMultiTurnAnthropic:
    async def test_cache_read_tokens_positive_from_turn_two(self) -> None:
        model = _FakeChatAnthropic(
            responses=[
                AIMessage(content="ok", usage_metadata=_usage(
                    input_tokens=2000, output_tokens=20, cache_read=0, cache_write=1800,
                )),
                AIMessage(content="ok", usage_metadata=_usage(
                    input_tokens=2050, output_tokens=20, cache_read=1800, cache_write=0,
                )),
                AIMessage(content="ok", usage_metadata=_usage(
                    input_tokens=2100, output_tokens=20, cache_read=1800, cache_write=0,
                )),
            ],
            openai_api_base=None,
        )
        transport = LangChainTransport(model, model_name="claude-sonnet-4-6")

        await _run_turns(transport, 3)

        # Every turn requested the same automatic cache_control breakpoint.
        assert all(
            kwargs == {"cache_control": {"type": "ephemeral"}}
            for kwargs in model.ainvoke_kwargs_by_turn
        )
        turn_1, turn_2, turn_3 = model.ainvoke_kwargs_by_turn
        assert len(model.ainvoke_kwargs_by_turn) == 3
        # Cold write on turn 1, reads recouping it from turn 2 onward —
        # exactly the scripted usage_metadata sequence above.
        assert model._responses[0].usage_metadata["input_token_details"]["cache_read"] == 0
        assert model._responses[1].usage_metadata["input_token_details"]["cache_read"] > 0
        assert model._responses[2].usage_metadata["input_token_details"]["cache_read"] > 0


class TestMultiTurnOpenAI:
    async def test_cached_tokens_positive_from_turn_two(self) -> None:
        model = _FakeChatOpenAI(
            responses=[
                AIMessage(content="ok", usage_metadata=_usage(
                    input_tokens=1500, output_tokens=20, cache_read=0, cache_write=0,
                )),
                AIMessage(content="ok", usage_metadata=_usage(
                    input_tokens=1550, output_tokens=20, cache_read=1200, cache_write=0,
                )),
            ],
            openai_api_base="https://api.openai.com/v1",
        )
        transport = LangChainTransport(
            model, model_name="gpt-5.6-terra", cache_key="org-1:user-1",
        )

        await _run_turns(transport, 2)

        assert all(
            kwargs == {"prompt_cache_key": "org-1:user-1"}
            for kwargs in model.ainvoke_kwargs_by_turn
        )
        assert model._responses[0].usage_metadata["input_token_details"]["cache_read"] == 0
        assert model._responses[1].usage_metadata["input_token_details"]["cache_read"] > 0


class TestFlagToggledOffMidSession:
    async def test_subsequent_calls_carry_no_directives_once_disabled(self) -> None:
        model = _FakeChatAnthropic(
            responses=[AIMessage(content="ok") for _ in range(3)],
            openai_api_base=None,
        )
        transport = LangChainTransport(model, model_name="claude-sonnet-4-6")

        await transport.complete([UserMessage(content="turn 1")])
        assert model.ainvoke_kwargs_by_turn[-1] == {"cache_control": {"type": "ephemeral"}}

        with patch(
            "app.agents.agent_loop.langchain_transport.resolve_cache_config",
            return_value=CacheConfig(enabled=False, source="feature_flag"),
        ):
            await transport.complete([UserMessage(content="turn 2")])
            await transport.complete([UserMessage(content="turn 3")])

        assert model.ainvoke_kwargs_by_turn[-2] == {}
        assert model.ainvoke_kwargs_by_turn[-1] == {}


class TestMinimalConfigAgentBelowFloor:
    async def test_unrecognized_model_sends_no_cache_kwarg_and_still_succeeds(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A legacy/unrecognized model resolves to `capability.mode="none"`
        (see `resolve_capability`'s conservative default) — no request
        mutation is attempted, the reason is logged, and the call still
        returns a normal response rather than erroring."""
        model = _FakeChatAnthropic(
            responses=[AIMessage(content="the answer")],
            openai_api_base=None,
        )
        transport = LangChainTransport(model, model_name="claude-2.1")

        with caplog.at_level("DEBUG", logger="app.agents.agent_loop.langchain_transport"):
            response = await transport.complete([UserMessage(content="hi")])

        assert model.ainvoke_kwargs_by_turn == [{}]
        assert response.message.text == "the answer"
        decision_lines = [r.getMessage() for r in caplog.records if "cache decision" in r.getMessage()]
        assert len(decision_lines) == 1
        assert "enabled=False" in decision_lines[0]
        assert "reason=capability_mode_none" in decision_lines[0]

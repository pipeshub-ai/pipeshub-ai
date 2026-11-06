"""The dispute checker keeps only quotes that dispute a stated detail, and
fails open."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage

from app.agents.agent_loop.coverage.dispute import disputed_quotes

_QUESTION = "Why did the Larkspur night crew pause the restock run?"
_DRAFT = "The run was paused for a compressor alarm [1](ref1). The log calls it a hold, not a pause."


def _llm(reply: object) -> MagicMock:
    llm = MagicMock()
    content = reply if isinstance(reply, str) else json.dumps(reply)
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    return llm


async def test_only_the_dispute_kind_is_kept() -> None:
    llm = _llm({"violations": [
        {"type": "opening_concedes", "quote": "x"},
        {"type": "disputes_stated_fact", "quote": "calls it a hold,  not a pause"},
        {"type": "B", "quote": "calls it a hold, not a pause"},
    ], "verdict": "revise"})
    assert await disputed_quotes(_QUESTION, _DRAFT, llm, timeout=1.0) == ["calls it a hold, not a pause"]
    prompt = llm.ainvoke.await_args.args[0][1].content
    assert _QUESTION in prompt and "(ref1)" not in prompt


async def test_an_unreadable_reply_disputes_nothing() -> None:
    assert await disputed_quotes(_QUESTION, _DRAFT, _llm("no json"), timeout=1.0) == []
    assert await disputed_quotes(_QUESTION, _DRAFT, _llm({"violations": "B"}), timeout=1.0) == []
    assert await disputed_quotes(_QUESTION, _DRAFT, None, timeout=1.0) == []


async def test_a_slow_model_disputes_nothing() -> None:
    async def _slow(*_args: object, **_kwargs: object) -> AIMessage:
        await asyncio.sleep(5)
        return AIMessage(content="{}")

    llm = MagicMock()
    llm.ainvoke = _slow
    assert await asyncio.wait_for(disputed_quotes(_QUESTION, _DRAFT, llm, timeout=0.05), timeout=2) == []

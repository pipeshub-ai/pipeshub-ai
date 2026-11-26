"""FU2-3: the scripted model's ``fail <text>`` directive: a provider that streams part of an answer, then fails.

Main #4442 retracts a model call's streamed text when the provider fails mid-answer. The E2E stack's model
(``deployment/e2e/fake_llm/server.py``) now does that on cue, so the human-test smoke can check the retraction on today's
loop and on a durable run. A provider client (the OpenAI SDK, as the loop's transport uses it) must see the text
arrive and then an error, never a finished answer.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openai
import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.agents.agent_loop.langchain_transport import (
    _is_transient_provider_error,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.testing.engines.script import (
    ChatMessage,
    Directive,
    ModelReply,
    ScriptedBrain,
    parse_directives,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SERVER = Path(__file__).resolve().parents[5] / "deployment" / "e2e" / "fake_llm" / "server.py"
KEY = "sk-fu2-test"
TOOLS = [{"type": "function", "function": {"name": "retrieval_search", "parameters": {"type": "object"}}}]
SCRIPT = "Answer, then lose the provider.\nfail The refund policy is thirty"


def _load() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ.setdefault("FAKE_LLM_API_KEY", KEY)
    os.environ.setdefault("FAKE_LLM_CONTROL_TOKEN", "control")
    spec = importlib.util.spec_from_file_location("fu2_fake_llm_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load()


@pytest.fixture
async def model_and_client() -> AsyncIterator[tuple[Any, TestClient]]:
    model = server.FakeModel()
    client = TestClient(TestServer(server.app(model)))
    await client.start_server()
    try:
        yield model, client
    finally:
        await client.close()


def _sdk(client: TestClient) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=os.environ["FAKE_LLM_API_KEY"], base_url=str(client.make_url("/v1")), max_retries=0)


def test_fail_is_a_directive_and_the_brain_answers_its_text_marked_to_fail() -> None:
    assert parse_directives(SCRIPT) == [Directive("fail", "The refund policy is thirty")]
    reply = ScriptedBrain().reply([ChatMessage("user", SCRIPT)], ["retrieval_search"], "c1")
    assert (reply.content, reply.fails_mid_stream) == ("The refund policy is thirty", True)
    assert ModelReply.from_json(reply.to_json()).fails_mid_stream is True
    assert "failsMidStream" not in ModelReply(content="fine").to_json()


def test_a_tool_directive_still_runs_before_the_failing_answer() -> None:
    script = 'tool retrieval_search {"q": 1}\nfail partial'
    first = ScriptedBrain().reply([ChatMessage("user", script)], ["retrieval_search"], "c1")
    assert [c.name for c in first.tool_calls] == ["retrieval_search"]
    assert first.fails_mid_stream is False


async def test_a_stream_delivers_the_text_then_fails(model_and_client: tuple[Any, TestClient]) -> None:
    model, client = model_and_client
    received: list[str] = []
    finish: list[str | None] = []
    async with _sdk(client) as sdk:
        stream = await sdk.chat.completions.create(
            model="e2e-llm", messages=[{"role": "user", "content": SCRIPT}], tools=TOOLS, stream=True  # type: ignore[arg-type]
        )
        with pytest.raises(openai.APIError) as failed:
            async for chunk in stream:
                for choice in chunk.choices:
                    received.append(choice.delta.content or "")
                    finish.append(choice.finish_reason)
    assert "".join(received) == "The refund policy is thirty"
    assert finish == [None]
    assert failed.value.code == server.FAULT_CODE
    # The loop retries and retracts only a transient in-stream error (#4442): the fault must read as one.
    assert _is_transient_provider_error(failed.value)
    assert [(r["kind"], r.get("fault")) for r in model.requests] == [("loop", "mid_stream")]


async def test_without_streaming_the_whole_call_fails(model_and_client: tuple[Any, TestClient]) -> None:
    _, client = model_and_client
    async with _sdk(client) as sdk:
        with pytest.raises(openai.InternalServerError):
            await sdk.chat.completions.create(
                model="e2e-llm", messages=[{"role": "user", "content": SCRIPT}], tools=TOOLS  # type: ignore[arg-type]
            )

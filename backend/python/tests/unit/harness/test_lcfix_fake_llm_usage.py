"""LCFIX: the scripted model reports usage as a provider counts it, so context management can act on it.

The prompt is the request body at about four bytes a token, the completion the reply's; ``usage <n>`` in the turn
names the prompt count outright. Before, every reply reported 10 prompt tokens, and no compaction threshold was ever
crossed (LIFECYCLE gallery, scenario 6). The server is the real one (``deployment/e2e/fake_llm/server.py``).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from harness_core.testing.engines.script import (
    ChatMessage,
    ModelReply,
    ScriptedBrain,
    ToolCall,
    scripted_usage,
)
from tests.unit.harness.test_qa5_fake_llm_loop import (  # pyright: ignore[reportPrivateUsage]
    _body,
    server,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    test_client = TestClient(TestServer(server.app(server.FakeModel())))
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


async def _usage(client: TestClient, body: dict[str, Any]) -> dict[str, int]:
    raw = json.dumps(body).encode()
    response = await client.post(
        "/v1/chat/completions",
        data=raw,
        headers={"Authorization": f"Bearer {os.environ['FAKE_LLM_API_KEY']}", "Content-Type": "application/json"},
    )
    assert response.status == 200
    usage = (await response.json())["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    return {"prompt": usage["prompt_tokens"], "completion": usage["completion_tokens"], "bytes": len(raw)}


async def test_lcfix_the_prompt_count_follows_the_request_body(client: TestClient) -> None:
    small = await _usage(client, _body([{"role": "user", "content": "say hi"}]))
    history = [{"role": "user", "content": "x" * 40_000}, {"role": "assistant", "content": "ok"}]
    large = await _usage(client, _body([*history, {"role": "user", "content": "say hi"}]))
    for asked in (small, large):
        assert asked["prompt"] == asked["bytes"] // 4
    assert large["prompt"] > 10_000 > small["prompt"]
    assert small["completion"] >= 1


async def test_lcfix_a_usage_directive_names_the_prompt_count(client: TestClient) -> None:
    asked = await _usage(client, _body([{"role": "user", "content": "usage 150000\nsay hi"}]))
    assert asked["prompt"] == 150_000


async def test_lcfix_a_usage_line_is_not_a_tool_call_and_unscripted_replies_are_counted_too(client: TestClient) -> None:
    unscripted = await _usage(client, _body([{"role": "user", "content": "Write a title for this chat"}], tools=False))
    assert unscripted["prompt"] == unscripted["bytes"] // 4


def test_lcfix_the_brain_counts_in_process_too() -> None:
    brain = ScriptedBrain()
    messages = [ChatMessage("user", "usage 4242\nshell ls\nsay done")]
    first = brain.reply(messages, ["bash"], "c1")
    assert [c.name for c in first.tool_calls] == ["bash"]
    assert first.usage.input_tokens == 4242
    plain = brain.reply([ChatMessage("user", "say " + "y" * 400)], [], "c2")
    assert plain.usage.input_tokens == len(json.dumps(ChatMessage("user", "say " + "y" * 400).to_json())) // 4
    assert plain.usage.output_tokens == len(plain.content) // 4


def test_lcfix_a_reply_with_calls_counts_their_arguments() -> None:
    reply = ModelReply(tool_calls=(ToolCall("c", "bash", json.dumps({"command": "z" * 400})),))
    assert scripted_usage([ChatMessage("user", "hi")], reply).output_tokens > 100

"""SJ-1..SJ-4 (E9): Claude Code's stream-json protocol over a process's stdin and stdout."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from harness_engines.claude_code.stream import (
    ClaudeControlError,
    ClaudeStream,
    ClaudeStreamEnded,
    StreamEnd,
    user_message,
)
from tests.unit.harness_engines.codex.test_rpc import (  # pyright: ignore[reportPrivateUsage]
    ScriptedProcess,
    _sent,
)


def _line(message: dict[str, object]) -> bytes:
    return json.dumps(message).encode() + b"\n"


async def test_sj1_lines_split_and_joined_and_noise_counted() -> None:
    process = ScriptedProcess()
    stream = ClaudeStream(process)
    stream.start()
    data = _line({"type": "system", "subtype": "init"}) + _line({"type": "assistant"})
    process.emit(data[:7])
    process.emit(data[7:] + b"not json\n[1]\n")
    assert (await stream.incoming.get())["type"] == "system"
    assert (await stream.incoming.get())["type"] == "assistant"
    process.end()
    assert isinstance(await stream.incoming.get(), StreamEnd)
    assert stream.unparsed_lines == 2
    await stream.close()


async def test_sj2_control_responses_correlate_out_of_order_and_errors_keep_their_message_out_of_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    process = ScriptedProcess()
    stream = ClaudeStream(process)
    stream.start()
    first = asyncio.ensure_future(stream.control("initialize", {"appendSystemPrompt": "x"}))
    second = asyncio.ensure_future(stream.control("interrupt"))
    await _sent(process, 2)
    assert [m["type"] for m in process.written] == ["control_request", "control_request"]
    assert process.written[0]["request"] == {"subtype": "initialize", "appendSystemPrompt": "x"}
    ids = [m["request_id"] for m in process.written]
    process.emit(_line({"type": "control_response", "response": {"subtype": "error", "request_id": ids[1], "error": "SECRET-DETAIL"}}))
    process.emit(_line({"type": "control_response", "response": {"subtype": "success", "request_id": ids[0], "response": {"ok": 1}}}))
    assert await first == {"ok": 1}
    with pytest.raises(ClaudeControlError) as refused:
        await second
    assert refused.value.subtype == "interrupt" and refused.value.detail == "SECRET-DETAIL"
    assert "SECRET-DETAIL" not in str(refused.value) and "SECRET-DETAIL" not in caplog.text
    assert stream.incoming.empty()
    await stream.close()


async def test_sj3_messages_and_the_engines_own_requests_reach_the_reader_in_order() -> None:
    process = ScriptedProcess()
    stream = ClaudeStream(process)
    stream.start()
    request = {"type": "control_request", "request_id": "cli-1", "request": {"subtype": "can_use_tool"}}
    process.emit(_line({"type": "assistant", "n": 1}) + _line(request) + _line({"type": "result"}))
    assert [(await stream.incoming.get()).get("type") for _ in range(3)] == ["assistant", "control_request", "result"]
    await stream.reply("cli-1", {"behavior": "deny"})
    hello = user_message("hello")
    await stream.send(hello)
    assert process.written == [
        {"type": "control_response", "response": {"subtype": "success", "request_id": "cli-1", "response": {"behavior": "deny"}}},
        {"type": "user", "message": {"role": "user", "content": "hello"}, "parent_tool_use_id": None, "uuid": hello["uuid"]},
    ]
    assert hello["uuid"] != user_message("hello")["uuid"]
    await stream.close()
    assert process.stdin_closed


async def test_sj4_the_end_of_stdout_fails_pending_requests_and_reaches_the_reader() -> None:
    process = ScriptedProcess()
    stream = ClaudeStream(process)
    stream.start()
    pending = asyncio.ensure_future(stream.control("initialize"))
    await _sent(process, 1)
    process.end()
    with pytest.raises(ClaudeStreamEnded):
        await pending
    assert isinstance(await stream.incoming.get(), StreamEnd)
    assert stream.ended
    with pytest.raises(ClaudeStreamEnded):
        await stream.control("interrupt")
    await stream.close()

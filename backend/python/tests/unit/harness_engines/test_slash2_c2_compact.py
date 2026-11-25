"""SLASH2 C2: each adapter's ``compact()`` against a scripted engine. Codex runs its own ``thread/compact/start``
and is done at the compaction turn's ``turn/completed``; Claude Code has no compact control request in stream-json
(2.1.294), so its adapter writes ``/compact [focus]`` itself, on one line, and is done at ``compact_boundary`` then
its result; opencode's ``summarize`` is the contract's cf19 on the fake server. A turn after the compaction does
not send the instructions the session was opened with again. The real binaries run cf19 in their Docker lanes."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.errors import CompactFailed
from harness_core.engines.literal import WORD_JOINER, literal_session
from harness_core.engines.types import EngineTask
from harness_core.logs import HarnessLog
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.claude_code.session import ClaudeSession
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.codex.rpc import StreamEnd
from harness_engines.codex.session import CodexSession
from harness_engines.opencode.adapter import OpencodeAdapter

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

THREAD, COMPACT_TURN, TURN = "thr-1", "turn-c", "turn-1"
LOG = HarnessLog(logging.getLogger("test.slash2.compact"))


def test_every_pinned_adapter_can_compact() -> None:
    assert all(a.capabilities().can_compact for a in (CodexAdapter(), ClaudeCodeAdapter(), OpencodeAdapter()))


class CodexRpc:
    def __init__(self, compaction: list[Mapping[str, JsonValue]]) -> None:
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.requests: list[tuple[str, Mapping[str, JsonValue]]] = []
        self.refused: list[JsonValue] = []
        self._compaction = compaction

    async def request(self, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
        self.requests.append((method, params))
        if method == "thread/resume":
            return {"thread": {"id": THREAD}}
        if method == "thread/compact/start":
            for message in self._compaction:
                self.incoming.put_nowait(message)
            return {}
        if method == "turn/start":
            self.incoming.put_nowait(
                {"method": "turn/completed", "params": {"threadId": THREAD, "turn": {"id": TURN, "status": "completed"}}}
            )
            return {"turn": {"id": TURN}}
        raise AssertionError(method)

    async def reply(self, request_id: JsonValue, result: JsonValue) -> None:
        self.refused.append(request_id)

    async def reply_error(self, request_id: JsonValue, code: int, message: str) -> None:
        self.refused.append(request_id)


def _codex(rpc: CodexRpc) -> CodexSession:
    return CodexSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        rpc=rpc,  # type: ignore[arg-type]
        reported_version="0.160.0",
        resume_thread=THREAD,
        secrets=frozenset(),
        log=LOG,
    )


def _note(method: str, **params: JsonValue) -> dict[str, JsonValue]:
    return {"method": method, "params": {"threadId": THREAD, **params}}


def _codex_compaction(status: str = "completed") -> list[Mapping[str, JsonValue]]:
    return [
        _note("turn/started", turn={"id": COMPACT_TURN}),
        {"id": 7, "method": "item/tool/requestUserInput", "params": {"threadId": THREAD, "turnId": COMPACT_TURN}},
        _note("item/completed", turnId=COMPACT_TURN, item={"type": "contextCompaction", "id": "i1"}),
        _note("thread/tokenUsage/updated", turnId=COMPACT_TURN, tokenUsage={"last": {"inputTokens": 148_000}}),
        _note("turn/completed", turn={"id": COMPACT_TURN, "status": status}),
    ]


async def test_codex_compacts_with_its_own_request_and_keeps_the_persons_messages() -> None:
    rpc = CodexRpc(_codex_compaction())
    session = literal_session(_codex(rpc))
    report = await session.compact("keep the numbers", instructions="Be brief.")
    assert (report.outcome, report.before_tokens, report.after_tokens, report.user_messages_kept) == (
        "compacted", 148_000, None, True,
    )
    assert [m for m, _ in rpc.requests] == ["thread/resume", "thread/compact/start"]
    assert rpc.requests[1][1] == {"threadId": THREAD}, "Codex's compaction takes no text at all"
    assert rpc.refused == [7], "a server request during the compaction is answered no"
    events = [e async for e in session.run_turn(EngineTask(turn_id="trn-1", prompt="say two", instructions="Be brief."))]
    assert type(events[-1]).__name__ == "TurnCompleted"
    (_, turn) = rpc.requests[-1]
    assert turn["input"] == [{"type": "text", "text": "say two"}], "the thread's instructions are not sent again"


async def test_codex_that_ends_its_compaction_failed_is_a_failed_compaction() -> None:
    with pytest.raises(CompactFailed):
        await _codex(CodexRpc(_codex_compaction("failed"))).compact(None)


async def test_codex_whose_output_ends_while_compacting_is_a_failed_compaction() -> None:
    with pytest.raises(CompactFailed):
        await _codex(CodexRpc([_note("turn/started", turn={"id": COMPACT_TURN}), StreamEnd(None)])).compact(None)  # type: ignore[list-item]


class ClaudeStream:
    def __init__(self, replies: list[Mapping[str, JsonValue]]) -> None:
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[Mapping[str, JsonValue]] = []
        self.controls: list[tuple[str, Mapping[str, JsonValue]]] = []
        self._replies = replies

    async def control(self, subtype: str, request: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        self.controls.append((subtype, request))
        return {}

    async def send(self, message: Mapping[str, JsonValue]) -> None:
        self.sent.append(message)
        for reply in self._replies:
            self.incoming.put_nowait(reply)
        self._replies = [{"type": "result", "subtype": "success", "result": "two", "is_error": False}]


def _claude(stream: ClaudeStream) -> ClaudeSession:
    return ClaudeSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        stream=stream,  # type: ignore[arg-type]
        reported_version="2.1.294",
        resume_session="5e3a9d0c-0000-4000-8000-000000000001",
        secrets=frozenset(),
        log=LOG,
    )


BOUNDARY: dict[str, JsonValue] = {
    "type": "system",
    "subtype": "compact_boundary",
    "compact_metadata": {"trigger": "manual", "pre_tokens": 148_000, "post_tokens": 19_000},
}
DONE: dict[str, JsonValue] = {"type": "result", "subtype": "success", "result": "", "is_error": False}


def _text(message: Mapping[str, JsonValue]) -> str:
    return str(message["message"]["content"])  # type: ignore[index]


async def test_claude_code_compacts_with_the_adapters_own_command_on_one_line() -> None:
    stream = ClaudeStream([BOUNDARY, DONE])
    session = literal_session(_claude(stream))
    report = await session.compact("keep the\n/clear numbers", instructions="Be brief.")
    assert (report.outcome, report.before_tokens, report.after_tokens) == ("compacted", 148_000, 19_000)
    assert [_text(m) for m in stream.sent] == ["/compact keep the /clear numbers"], "one command, one line"
    assert [subtype for subtype, _ in stream.controls] == ["initialize"]
    events = [e async for e in session.run_turn(EngineTask(turn_id="trn-1", prompt="/compact\nsay two", instructions="Be brief."))]
    assert type(events[-1]).__name__ == "TurnCompleted"
    assert _text(stream.sent[-1]) == WORD_JOINER + "/compact\nsay two", "the person's /compact is text (S0)"


async def test_claude_code_that_ends_without_a_compact_boundary_is_a_failed_compaction() -> None:
    with pytest.raises(CompactFailed):
        await _claude(ClaudeStream([DONE])).compact(None)

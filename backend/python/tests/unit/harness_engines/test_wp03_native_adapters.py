"""WP-03 on every adapter: an unknown native event is kept, never a crash (EV-13, C2.40), and a turn's native
transcript re-parsed by a fresh mapper yields the same canonical events (UP-07, C2.39).

Codex, Claude Code and opencode replay their recorded fixtures; ACP has none recorded, so its turn is the
protocol's own shapes, as the fake agent sends them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.events import NativeUnknown, TextDelta, TurnCompleted
from harness_core.engines.native import NativeTape, RecordsNative, reparse
from harness_core.engines.types import EngineTask
from harness_core.logs import HarnessLog
from harness_engines.acp.mapper import AcpEventMapper
from harness_engines.acp.permissions import SessionPolicy
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.codex.session import CodexSession
from harness_engines.opencode.mapper import OpencodeEventMapper

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.native import TranscriptPart
    from harness_core.engines.types import JsonValue

HERE = Path(__file__).parent
TURN = "trn_wp03"
POLICY = SessionPolicy(
    agent="opencode",
    allowed=frozenset(),
    native_names={},
    plan_tool=None,
    gateway_prefix="pipeshub_",
    mcp_server="pipeshub",
    tools_url="",
    tools_placeholder="",
    client_fs_read=False,
    client_fs_write=False,
)


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _taped() -> NativeTape:
    tape = NativeTape()
    tape.begin(TURN)
    return tape


def _transcript(tape: NativeTape) -> bytes:
    parts: list[TranscriptPart] = tape.finish()
    assert parts and parts[-1].final and not parts[-1].truncated
    return b"".join(p.data for p in parts)


# ---- Codex ----


def _codex(name: str, tape: NativeTape | None) -> tuple[list[EngineEvent], dict[str, str]]:
    messages = _lines(HERE / "codex" / "fixtures" / f"{name}.jsonl")
    ids = {
        "thread": next(m["result"]["thread"]["id"] for m in messages if m.get("id") == 12 and "method" not in m),
        "turn": next(m["result"]["turn"]["id"] for m in messages if m.get("id") == 13 and "method" not in m),
    }
    mapper = CodexEventMapper(thread_id=ids["thread"], turn_id=TURN)
    mapper.tape = tape
    mapper.begin(ids["turn"])
    events: list[EngineEvent] = []
    for message in messages:
        if "method" in message and not mapper.done:
            events.extend(mapper.feed(message))
    return events, ids


@pytest.mark.parametrize("name", ["tool", "shell", "spawn", "interrupt"])
def test_up07_codex_a_recorded_turn_reparses_to_the_same_events(name: str) -> None:
    tape = _taped()
    events, ids = _codex(name, tape)
    assert any(isinstance(e, TurnCompleted) for e in events) or name in ("spawn", "interrupt")
    assert reparse(_transcript(tape), CodexEventMapper(thread_id=ids["thread"], turn_id=TURN)) == events


def test_ev13_codex_an_unknown_method_is_kept_and_the_turn_maps_on() -> None:
    mapper = CodexEventMapper(thread_id="thr", turn_id=TURN)
    mapper.begin("t1")
    newer = {"method": "item/hologram/delta", "params": {"threadId": "thr", "turnId": "t1", "delta": "?"}}
    [unknown] = mapper.feed(newer)
    assert isinstance(unknown, NativeUnknown) and unknown.kind == "item/hologram/delta"
    assert json.loads(unknown.raw) == newer and unknown.size_bytes == len(unknown.raw)
    assert mapper.feed({"method": "turn/started", "params": {"threadId": "thr"}}) == []
    [text] = mapper.feed({"method": "item/agentMessage/delta", "params": {"threadId": "thr", "turnId": "t1", "itemId": "m", "delta": "hi"}})
    assert isinstance(text, TextDelta) and text.meta.seq == 1


class _FakeRpc:
    def __init__(self, messages: list[Mapping[str, JsonValue]], ids: dict[str, str]) -> None:
        self.incoming: asyncio.Queue[Mapping[str, JsonValue]] = asyncio.Queue()
        for message in messages:
            self.incoming.put_nowait(message)
        self._ids = ids

    async def request(self, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
        if method == "thread/start":
            return {"thread": {"id": self._ids["thread"]}}
        return {"turn": {"id": self._ids["turn"]}}

    async def reply(self, request_id: JsonValue, result: JsonValue) -> None:
        return None

    async def reply_error(self, request_id: JsonValue, code: int, message: str) -> None:
        return None


async def test_up07_codex_session_tapes_its_turn_and_the_transcript_reparses_to_what_it_yielded() -> None:
    messages = _lines(HERE / "codex" / "fixtures" / "tool.jsonl")
    _, ids = _codex("tool", None)
    session = CodexSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        rpc=_FakeRpc([m for m in messages if "method" in m], ids),  # type: ignore[arg-type]
        reported_version="0.160.0",
        resume_thread=None,
        secrets=frozenset(),
        log=HarnessLog(logging.getLogger("test.wp03.codex")),
    )
    assert isinstance(session, RecordsNative)
    tape = _taped()
    session.tap_native(tape)
    yielded = [e async for e in session.run_turn(EngineTask(turn_id=TURN, prompt="go", instructions=""))]
    assert isinstance(yielded[-1], TurnCompleted)
    assert reparse(_transcript(tape), CodexEventMapper(thread_id=ids["thread"], turn_id=TURN)) == yielded


# ---- Claude Code ----


@pytest.mark.parametrize("name", ["text", "bash", "mcp", "write", "agent", "denied", "error", "interrupt"])
def test_up07_claude_code_a_recorded_turn_reparses_to_the_same_events(name: str) -> None:
    tape = _taped()
    mapper = ClaudeEventMapper(turn_id=TURN, tape=tape)
    if name == "interrupt":
        mapper.mark_interrupted()
    events: list[EngineEvent] = []
    for message in _lines(HERE / "claude_code" / "fixtures" / f"{name}.jsonl"):
        events += mapper.feed(message)
        if mapper.done:
            break
    assert reparse(_transcript(tape), ClaudeEventMapper(turn_id=TURN)) == events


def test_ev13_claude_code_an_unknown_message_type_is_kept_and_the_turn_maps_on() -> None:
    mapper = ClaudeEventMapper(turn_id=TURN)
    [unknown] = mapper.feed({"type": "hologram", "payload": {"x": 1}})
    assert isinstance(unknown, NativeUnknown) and unknown.kind == "hologram"
    assert mapper.feed({"type": "rate_limit_event"}) == []
    done = mapper.feed({"type": "result", "subtype": "success", "result": "ok", "usage": {}})
    assert isinstance(done[-1], TurnCompleted) and done[-1].reason == "completed"


# ---- opencode ----


@pytest.mark.parametrize(
    "name",
    ["native-locked-mcp_call", "native-locked-bash_ask", "native-default-task", "native-locked-task", "native-locked-title"],
)
def test_up07_opencode_a_recorded_turn_reparses_to_the_same_events(name: str) -> None:
    tape = _taped()
    mapper = OpencodeEventMapper(session_id="ses_001", turn_id=TURN)
    mapper.tape = tape
    events: list[EngineEvent] = []
    for payload in _lines(HERE / "opencode" / "fixtures" / f"{name}.events.jsonl"):
        events.extend(mapper.feed(payload))
        if mapper.done:
            break
    assert reparse(_transcript(tape), OpencodeEventMapper(session_id="ses_001", turn_id=TURN)) == events


def test_ev13_opencode_an_unknown_bus_event_is_kept_and_a_tui_event_is_quiet() -> None:
    mapper = OpencodeEventMapper(session_id="ses_001", turn_id=TURN)
    [unknown] = mapper.feed({"type": "session.hologram", "properties": {"sessionID": "ses_001"}})
    assert isinstance(unknown, NativeUnknown) and unknown.kind == "session.hologram"
    assert mapper.feed({"type": "tui.toast.show", "properties": {}}) == []
    assert mapper.feed({"type": "session.status", "properties": {"sessionID": "ses_001", "status": {"type": "busy"}}}) == []


# ---- ACP ----


def _acp(tape: NativeTape | None, *, interrupt: bool = False) -> tuple[list[EngineEvent], AcpEventMapper]:
    mapper = AcpEventMapper(turn_id=TURN, policy=POLICY)
    mapper.tape = tape
    events: list[EngineEvent] = []
    events += mapper.update({"sessionUpdate": "agent_message_chunk", "messageId": "m1", "content": {"type": "text", "text": "Let me look."}})
    events += mapper.update({"sessionUpdate": "available_commands_update", "availableCommands": []})
    events += mapper.update({"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "ls", "kind": "execute", "status": "pending", "rawInput": {"command": "ls"}})
    events.append(mapper.ask("acp-ask-1", {"toolCallId": "c1", "title": "ls", "kind": "execute"}, "bash"))
    events += mapper.update({"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed", "rawOutput": {"exitCode": 0}})
    events += mapper.update({"sessionUpdate": "hologram_update", "x": 1})
    events += mapper.notice("_vendor/ping", {"method": "_vendor/ping", "params": {}})
    if interrupt:
        mapper.mark_interrupted()
    events += mapper.completed({"stopReason": "cancelled" if interrupt else "end_turn", "usage": {"inputTokens": 5, "outputTokens": 2}})
    return events, mapper


@pytest.mark.parametrize("interrupt", [False, True])
def test_up07_acp_a_turn_reparses_to_the_same_events(interrupt: bool) -> None:
    tape = _taped()
    events, _ = _acp(tape, interrupt=interrupt)
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].reason == ("interrupted" if interrupt else "completed")
    assert reparse(_transcript(tape), AcpEventMapper(turn_id=TURN, policy=POLICY)) == events


def test_ev13_acp_an_unknown_update_and_an_undefined_notification_are_kept_and_the_turn_completes() -> None:
    events, _ = _acp(None)
    unknown = [e for e in events if isinstance(e, NativeUnknown)]
    assert [u.kind for u in unknown] == ["hologram_update", "_vendor/ping"]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"


def test_c239_every_session_records_native_transcripts() -> None:
    from harness_engines.acp.session import AcpSession
    from harness_engines.claude_code.session import ClaudeSession
    from harness_engines.opencode.session import OpencodeSession

    for session in (AcpSession, ClaudeSession, CodexSession, OpencodeSession):
        assert callable(getattr(session, "tap_native", None)), session

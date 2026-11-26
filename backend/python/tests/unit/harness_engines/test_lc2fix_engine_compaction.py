"""LC2FIX-3: each engine's own compaction mid-turn becomes one ``ContextCompacted``, with the counts the engine gives:
Claude Code's ``compact_boundary``, opencode's ``session.compacted`` after its summary message (which is never the
turn's answer), Codex's ``contextCompaction`` item and/or its older ``thread/compacted`` notice (one compaction)."""

from __future__ import annotations

from typing import Any

from harness_core.engines.events import ContextCompacted, MessageCompleted, TextDelta
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.opencode.mapper import OpencodeEventMapper


def _compactions(events: list[Any]) -> list[tuple[int | None, int | None, bool]]:
    return [(e.before_tokens, e.after_tokens, e.user_messages_kept) for e in events if isinstance(e, ContextCompacted)]


def test_claude_code_compact_boundary() -> None:
    mapper = ClaudeEventMapper(turn_id="trn_1")
    boundary = {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "auto", "pre_tokens": 260000}}
    assert _compactions(mapper.feed(boundary)) == [(260000, None, False)]
    helper = {**boundary, "parent_tool_use_id": "tu_1"}
    assert _compactions(mapper.feed(helper)) == [], "a helper's own compaction is not the session's"


def _codex() -> CodexEventMapper:
    mapper = CodexEventMapper(thread_id="th_1", turn_id="trn_1")
    mapper.begin("cx_1")
    usage = {"threadId": "th_1", "turnId": "cx_1", "tokenUsage": {"last": {"inputTokens": 260000, "outputTokens": 9}}}
    _ = mapper.feed({"method": "thread/tokenUsage/updated", "params": usage})
    return mapper


ITEM = {"method": "item/completed", "params": {"threadId": "th_1", "turnId": "cx_1", "item": {"type": "contextCompaction", "id": "i1"}}}
NOTICE = {"method": "thread/compacted", "params": {"threadId": "th_1", "turnId": "cx_1"}}


def test_codex_one_compaction_whichever_signal_arrives_and_both() -> None:
    for signals in ([ITEM], [NOTICE], [ITEM, NOTICE], [NOTICE, ITEM]):
        mapper = _codex()
        events = [e for s in signals for e in mapper.feed(s)]
        assert _compactions(events) == [(260000, None, True)], signals
    mapper = _codex()
    events = [e for s in (ITEM, NOTICE, ITEM) for e in mapper.feed(s)]
    assert len(_compactions(events)) == 2, "a second compaction in the turn is its own"


def _opencode(kind: str, **props: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"type": kind, "properties": {"sessionID": "ses_1", **props}}


def test_opencode_session_compacted_and_its_summary_is_not_the_answer() -> None:
    mapper = OpencodeEventMapper(session_id="ses_1", turn_id="trn_1")
    _ = mapper.feed(_opencode("session.status", status={"type": "busy"}))
    info = {"id": "msg_s", "role": "assistant", "summary": True, "time": {"created": 1}, "tokens": {"input": 250000, "output": 1800}}
    events = mapper.feed(_opencode("message.updated", info=info))
    part = {"type": "text", "id": "p1", "messageID": "msg_s", "text": "Summary of the work so far"}
    events += mapper.feed(_opencode("message.part.delta", field="text", partID="p1", messageID="msg_s", delta="Summary"))
    events += mapper.feed(_opencode("message.part.updated", part=part))
    events += mapper.feed(_opencode("message.updated", info={**info, "time": {"created": 1, "completed": 2}}))
    events += mapper.feed(_opencode("session.compacted"))
    assert _compactions(events) == [(250000, 1800, False)]
    assert [e for e in events if isinstance(e, (MessageCompleted, TextDelta))] == []
    events = mapper.feed(_opencode("session.status", status={"type": "idle"}))
    assert [e.final_text for e in events if hasattr(e, "final_text")] == [""]

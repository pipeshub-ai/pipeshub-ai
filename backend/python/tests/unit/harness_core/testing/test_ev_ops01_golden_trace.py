"""EV-OPS-01 / C17.07: a run's journal captured as a redacted golden case (``harness_core.testing.replay.trace_redactor``).

GTR-1 a captured case holds none of the run's secrets, addresses, phone numbers or words; ids are pseudonymised and
      the structure the fold reads (event types, seq, tool ids, statuses, digests) is kept.
GTR-2 the case replays to the same fold, cursor and provenance facts as the original journal.
GTR-3 redaction is idempotent: capturing a case again leaves it unchanged, and ``verify`` proves it.
GTR-4 a capture that cannot be proven writes nothing: a canary that survives, a journal that does not replay, an
      export with several runs and none named, an incident written as a description.
GTR-5 inline content is decoded, redacted by the same rule and re-encoded with a matching hash and size.
GTR-6 the CLI captures an export (``{"events": [...]}``, an array, or JSON lines) into a case and ``check``s it; a
      refused capture exits 1 and writes no file.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import pytest

from harness_core.contracts.canonical_json import canonical_json
from harness_core.journal.inline import content_digest
from harness_core.journal.types import JournalEvent
from harness_core.testing.replay.__main__ import main as replay_cli
from harness_core.testing.replay.trace_redactor import (
    GoldenCase,
    RedactionNotProven,
    TraceRedactor,
    capture,
    leftovers,
    load_export,
    replay_signature,
    verify,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

KEY = b"golden-trace-unit-tests"
RUN = "6f1c2e0a-9b7d-4c1e-a2f3-0d9e8c7b6a51"
ORG = "64b7f0c2e1a9d3f4b5c6d7e8"
SESSION = "ses-7Hq2LmZ9"
TOKEN = "xoxb-2231-99887766-AbCdEfGhIjKl"
GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
EMAIL = "dana.lee@corp.example"
PHONE = "+44 20 7946 0958"
WORDS = "Ask Dana about the Henderson merger before Friday"


def _inline(value: object) -> dict[str, JsonValue]:
    data = canonical_json(value)
    return {"ref": "inline:" + base64.urlsafe_b64encode(data).decode("ascii"), "hash": content_digest(data), "size": len(data)}


def _event(seq: int, type_: str, data: dict[str, JsonValue], *, writer: str = "worker", critical: bool = True) -> dict[str, JsonValue]:
    envelope: dict[str, JsonValue] = {
        "orgId": ORG, "sessionId": SESSION, "streamId": SESSION, "seq": seq, "runId": RUN, "type": type_,
        "critical": critical, "ts": f"2026-11-01T10:00:{seq:02d}.000Z", "writer": writer, "data": data,
    }
    if writer == "worker":
        envelope |= {"attemptId": f"{RUN}.a1", "fence": 1}
    return envelope


def _digest(args: object) -> str:
    return content_digest(canonical_json(args))


def journal() -> list[dict[str, JsonValue]]:
    """A completed two-turn run: a Slack read whose result carries a token, an address, a phone number and a URL
    with a password, then an answer quoting them."""
    read_args = {"channel": "#ops", "api_key": GITHUB}
    result = {"messages": [{"user": "dana", "text": f"rotate {TOKEN}; call {PHONE} or {EMAIL}",
                            "link": "https://admin:hunter22@ops.corp.example/runbook"}]}
    return [
        _event(1, "run.admitted", {"runKind": "interactive", "principalRef": "p_user"}, writer="node"),
        _event(2, "run.claimed", {"attempt": 1, "fence": 1, "leaseMs": 30000}, writer="node"),
        _event(3, "message.completed", {"role": "user", "textRef": _inline(WORDS), "isFinal": False}),
        _event(4, "turn.started", {"turn": 1}),
        _event(5, "model.request", {"contextDigest": _digest(1), "route": {"provider": "openai", "model": "gpt-x"}, "modelAttempt": 1}),
        _event(6, "model.response", {
            "messageRef": _inline({"text": "", "toolCalls": [{"id": "call-1", "name": "slack__read_channel", "args": read_args}]}),
            "toolCalls": [{"callId": "call-1", "modelName": "slack__read_channel", "argsDigest": _digest(read_args)}],
            "usage": {"inputTokens": 900, "outputTokens": 20, "cacheReadTokens": 0, "cacheWriteTokens": 0},
            "provider": "openai", "transport": "responses",
        }),
        _event(7, "x.pipeshub.tool.call_presented", {"callId": "call-1", "toolName": "slack__read_channel",
                                                     "argsJson": json.dumps(read_args)}, critical=False),
        _event(8, "tool.intent", {"invocationId": "t1.0", "toolId": "pipeshub@slack/read_channel",
                                  "argsDigest": _digest(read_args), "effect": "read",
                                  "sink": {"egress": "self", "argsLeaveBoundary": False}}),
        _event(9, "tool.outcome", {"invocationId": "t1.0", "status": "ok", "resultRef": _inline(result),
                                   "labels": {"integrity": "internal"}}),
        _event(10, "x.pipeshub.provenance.recorded", {
            "invocationId": "t1.0", "toolId": "pipeshub@slack/read_channel", "rule": "connector",
            "provenance": {"readers": "principals", "integrity": "first_party", "sources": [
                {"kind": "connector", "connector": "slack", "externalRecordId": "C024BE91L-1712"},
                {"kind": "web", "url": "https://ops.corp.example/runbook?token=s3cr3tvalue"},
            ]},
        }, critical=False),
        _event(11, "turn.completed", {"turn": 1}),
        _event(12, "turn.started", {"turn": 2}),
        _event(13, "model.request", {"contextDigest": _digest(2), "route": {"provider": "openai", "model": "gpt-x"}, "modelAttempt": 2}),
        _event(14, "model.response", {
            "messageRef": _inline({"text": f"Call {PHONE} or write to {EMAIL}.", "toolCalls": []}),
            "toolCalls": [], "usage": {"inputTokens": 1200, "outputTokens": 40, "cacheReadTokens": 800, "cacheWriteTokens": 0},
            "provider": "openai", "transport": "responses",
        }),
        _event(15, "message.completed", {"role": "assistant", "textRef": _inline(f"Call {PHONE} or write to {EMAIL}."), "isFinal": True}),
        _event(16, "turn.completed", {"turn": 2}),
        _event(17, "run.completed", {"outcome": "answered"}),
    ]


PLANTED = (TOKEN, GITHUB, EMAIL, PHONE, "hunter22", "s3cr3tvalue", "Henderson", "corp.example", RUN, ORG, SESSION)


def _all_text(case: GoldenCase) -> str:
    """The case as written, with every inline ref's content decoded (what a reader of the file could recover)."""
    found = [case.to_json()]

    def walk(value: object) -> None:
        if isinstance(value, str) and value.startswith("inline:"):
            found.append(base64.urlsafe_b64decode(value.removeprefix("inline:")).decode("utf-8"))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(case.events)
    return "\n".join(found)


def test_gtr1_the_case_keeps_structure_and_drops_secrets_people_and_words() -> None:
    raw = journal()
    assert leftovers(raw), "the fixture must carry something to redact"

    case = capture(raw, case="slack-read-answer", incident="INC-4211", key=KEY)

    text = _all_text(case)
    for planted in PLANTED:
        assert planted not in text, planted
    assert leftovers(case.events) == []
    assert [e["type"] for e in case.events] == [e["type"] for e in raw]
    assert [e["seq"] for e in case.events] == [e["seq"] for e in raw]
    first = case.events[0]
    assert (first["orgId"], first["sessionId"], first["runId"]) == ("org-1", "ses-1", "run-1")
    assert case.events[1]["writer"] == "node" and case.events[7]["attemptId"] == "run-1.a1"
    intent = case.events[7]["data"]
    assert isinstance(intent, dict)
    assert intent["toolId"] == "pipeshub@slack/read_channel" and intent["argsDigest"] == _digest({"channel": "#ops", "api_key": GITHUB})
    assert case.manifest.run_id == "run-1" and case.manifest.incident == "INC-4211" and case.manifest.source_events == 17


def test_gtr2_the_case_replays_as_the_original_did() -> None:
    raw = journal()
    original = replay_signature([JournalEvent.from_envelope(e) for e in raw], RUN)

    case = capture(raw, case="slack-read-answer", key=KEY)

    assert replay_signature(case.journal(), case.manifest.run_id) == original == case.expected
    assert original["status"] == "completed" and original["outcome"] == "answered"
    assert [i["toolId"] for i in original["invocations"]] == ["pipeshub@slack/read_channel"]  # type: ignore[index, union-attr]
    assert original["provenance"] == [{"invocationId": "t1.0", "rule": "connector", "recorded": True, "readers": "principals",
                                       "integrity": "first_party", "sources": ["connector", "web"]}]


def test_gtr3_redaction_is_idempotent_and_verified() -> None:
    case = capture(journal(), case="slack-read-answer", key=KEY)

    assert TraceRedactor(key=b"another key").events(case.events) == case.events
    assert verify(case) == []
    again = capture(case.events, case="slack-read-answer", key=b"another key")
    assert again.events == case.events and again.expected == case.expected


def test_gtr3_equal_text_gets_equal_stand_ins_within_a_case() -> None:
    redactor = TraceRedactor(key=KEY)
    first, second, other = (redactor.value(v, "text") for v in ("same words", "same words", "other words"))
    assert first == second != other
    assert redactor.value(EMAIL, "recipient") == redactor.value(EMAIL, "argsJson")
    assert redactor.value("Az09Qw8rTy7uIo6pLk5jHg4fDs3a", "query") != "Az09Qw8rTy7uIo6pLk5jHg4fDs3a", (
        "an unknown-shaped token outside a structural key is replaced, not trusted"
    )
    assert redactor.value("anything", "password") == "<redacted:secret>"
    assert str(redactor.value("https://drive.example.com/d/123", "link")).startswith("https://example.invalid/")


def test_gtr4_a_surviving_canary_refuses_the_capture() -> None:
    raw = journal()
    raw[0]["data"]["principalRef"] = "p_hendersonco"  # type: ignore[index]

    with pytest.raises(RedactionNotProven) as refused:
        capture(raw, case="slack-read-answer", canaries=["hendersonco"], key=KEY)
    assert any("canary" in reason for reason in refused.value.reasons)


def test_gtr4_a_journal_that_does_not_replay_is_refused() -> None:
    raw = journal()
    raw[8]["data"]["status"] = 42  # type: ignore[index]

    with pytest.raises(RedactionNotProven) as refused:
        capture(raw, case="slack-read-answer", key=KEY)
    assert "does not replay" in str(refused.value)


def test_gtr4_several_runs_need_one_named_and_an_incident_is_an_id() -> None:
    raw = journal()
    other = [dict(e, runId="run-other", seq=int(e["seq"]) + 100) for e in raw]  # type: ignore[arg-type]

    with pytest.raises(RedactionNotProven, match="name one with --run"):
        capture([*raw, *other], case="two-runs", key=KEY)
    named = capture([*raw, *other], case="two-runs", run_id=RUN, key=KEY)
    assert {e["runId"] for e in named.events} == {"run-1"}
    with pytest.raises(RedactionNotProven, match="incident is an id"):
        capture(raw, case="slack-read-answer", incident="Dana's merger leak", key=KEY)


def test_gtr5_inline_content_is_redacted_and_re_encoded_with_its_hash() -> None:
    case = capture(journal(), case="slack-read-answer", key=KEY)
    outcome = case.events[8]["data"]
    assert isinstance(outcome, dict)
    ref = outcome["resultRef"]
    assert isinstance(ref, dict)
    data = base64.urlsafe_b64decode(str(ref["ref"]).removeprefix("inline:"))

    assert ref["hash"] == content_digest(data) and ref["size"] == len(data)
    decoded = json.loads(data)
    assert set(decoded["messages"][0]) == {"user", "text", "link"}
    assert decoded["messages"][0]["text"].startswith("<text:")


@pytest.mark.parametrize("shape", ["object", "array", "lines"])
def test_gtr6_the_cli_captures_an_export_and_checks_the_case(tmp_path: Path, shape: str, capsys: pytest.CaptureFixture[str]) -> None:
    raw = journal()
    export = tmp_path / "export.json"
    text = {
        "object": json.dumps({"events": raw}),
        "array": json.dumps(list(reversed(raw))),
        "lines": "\n".join(json.dumps(e) for e in raw),
    }[shape]
    export.write_text(text, encoding="utf-8")
    assert load_export(text) == sorted(raw, key=lambda e: int(e["seq"]))  # type: ignore[arg-type]
    target = tmp_path / "golden" / "slack-read-answer.json"

    assert replay_cli(["capture", str(export), str(target), "--case", "slack-read-answer", "--incident", "INC-4211"]) == 0
    assert "run run-1" in capsys.readouterr().out
    case = GoldenCase.load(target)
    for planted in PLANTED:
        assert planted not in _all_text(case)
    assert replay_cli(["check", str(target)]) == 0


def test_gtr6_a_refused_capture_exits_1_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = journal()
    raw[0]["data"]["principalRef"] = "p_hendersonco"  # type: ignore[index]
    export = tmp_path / "export.json"
    export.write_text(json.dumps(raw), encoding="utf-8")
    target = tmp_path / "case.json"

    assert replay_cli(["capture", str(export), str(target), "--case", "x", "--canary", "hendersonco"]) == 1
    assert not target.exists()
    assert "golden trace refused" in capsys.readouterr().err


def test_gtr6_check_fails_a_tampered_case(tmp_path: Path) -> None:
    case = capture(journal(), case="slack-read-answer", key=KEY)
    tampered = case.model_copy(update={"events": [*case.events[:-1], {**case.events[-1], "data": {"outcome": "failed"}}]})
    path = tmp_path / "case.json"
    tampered.save(path)
    assert replay_cli(["check", str(path)]) == 1
    leaked = case.model_copy(update={"events": [{**case.events[0], "data": {"runKind": "interactive", "principalRef": EMAIL}}, *case.events[1:]]})
    assert any("email" in p for p in verify(leaked))

"""WP91-ACPCOMPACT (C3.67, MG-10) in the ACP driver, with the scripted ACP peer.

- **Summary and restart.** ACP has no request to compact a session, and an agent that fails the prompt on a context
  overflow (Gemini CLI 0.63; the fake agent does the same) would fail the next message the same way. The driver's
  ``restart_from_summary`` opens a fresh ACP session in the same process and sandbox, its first prompt the conversation
  so far as the driver saw it (each turn's request and answer, newest kept: the recap a session that cannot be loaded
  resumes from), and the retried request after it once. The run's instructions stay; the engine's own transcript of
  tool output, which is what overflowed, does not come along.
- **A compaction notice.** opencode over ACP compacts and retries by itself, but ACP carries no notice of it. Where the
  profile names opencode's loopback API (``compactionNotice: opencode``, beside ``mcp.status``), each turn's end asks it
  for the session's summary messages (``GET /session/{id}/message``, an assistant message marked ``summary``): one the
  driver has not seen is one ``ContextCompacted``, before the turn's end, with the summary's token counts.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.chain import engine_session_chain
from harness_core.engines.errors import CompactFailed
from harness_core.engines.events import (
    ContextCompacted,
    EngineError,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.ports import RestartsFromSummary
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.testing.engines.contract import collect, open_rig
from harness_engines.acp.compaction import AgentCompaction
from harness_engines.acp.profiles import AcpProfileInvalid, parse_profile
from harness_engines.acp.session import AcpSession
from harness_engines.acp.state import decode_state
from harness_engines.acp.testing.fake_agent import INSTRUCTIONS_PATH
from harness_engines.acp.testing.profile import fake_entry
from tests.unit.harness_engines.acp.conftest import AcpHarness

if TYPE_CHECKING:
    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import JsonValue
    from harness_core.testing.engines.contract import Rig
    from harness_engines.opencode_api.http import OpencodeHttp

RUN = "run-wp91-acp-compaction"
INSTRUCTIONS = "Answer in one word."
OPENCODE: dict[str, JsonValue] = {"server": "tools", "toolName": "{server}_{tool}", "status": {"api": "opencode", "port": 4096}}


def _sent(harness: AcpHarness, method: str) -> list[dict[str, JsonValue]]:
    return [
        json.loads(json.dumps(m["params"]))
        for f in harness.fakes.values()
        for c in f.connections
        for m in c.received
        if m.get("method") == method
    ]


def _prompt_text(params: dict[str, JsonValue]) -> str:
    blocks = params["prompt"]
    assert isinstance(blocks, list)
    return "\n".join(str(b["text"]) for b in blocks if isinstance(b, dict))


async def _run(rig: Rig, task: EngineTask | FollowUp) -> list[EngineEvent]:
    chain = engine_session_chain(rig.session)
    return [event async for event in chain.run_turn(task)]


class TestSummaryAndRestart:
    def test_wp91_acpcompact_1_the_acp_session_offers_a_restart_and_still_no_compact_on_request(self) -> None:
        assert issubclass(AcpSession, RestartsFromSummary)
        # The person's /compact is not offered for ACP engines: that stays the command catalog's to change.
        assert AcpHarness().adapter.capabilities().can_compact is False

    async def test_wp91_acpcompact_2_an_overflowed_turn_is_retried_in_a_fresh_session_seeded_with_the_summary(self) -> None:
        harness = AcpHarness()
        rig = await open_rig(harness)
        first = await _run(rig, EngineTask(turn_id_for(RUN, 0), "say one", instructions=INSTRUCTIONS))
        assert isinstance(first[-1], TurnCompleted) and first[-1].final_text.strip() == "one"
        events = await _run(rig, FollowUp(turn_id_for(RUN, 1), "overflow\nsay recovered"))
        names = [type(e).__name__ for e in events]
        assert rig.gateway.brain.overflowed  # pyright: ignore[reportAttributeAccessIssue]
        assert sum(isinstance(e, ContextCompacted) for e in events) == 1, names
        assert not [e for e in events if isinstance(e, EngineError)], names
        ended = events[-1]
        assert isinstance(ended, TurnCompleted) and ended.reason == "completed" and ended.final_text.split()[-1:] == ["recovered"]
        news = _sent(harness, "session/new")
        assert len(news) == 2
        prompts = _sent(harness, "session/prompt")
        assert len(prompts) == 3
        assert prompts[2]["sessionId"] != prompts[1]["sessionId"]
        seeded = _prompt_text(prompts[2])
        # The summary carries the earlier exchange, and the retried request once (as the prompt, not in the recap).
        assert "User: say one" in seeded and "You: one" in seeded
        assert seeded.count("overflow") == 1 and seeded.rstrip().endswith("say recovered")
        # The run's instructions stay with the new session.
        assert rig.sandbox.files[INSTRUCTIONS_PATH].decode() == INSTRUCTIONS  # pyright: ignore[reportAttributeAccessIssue]
        await rig.session.close()

    async def test_wp91_acpcompact_3_the_state_after_a_restart_is_the_new_session(self) -> None:
        harness = AcpHarness()
        rig = await open_rig(harness)
        _ = await _run(rig, EngineTask(turn_id_for(RUN, 0), "say one", instructions=INSTRUCTIONS))
        _ = await _run(rig, FollowUp(turn_id_for(RUN, 1), "overflow\nsay recovered"))
        state = decode_state(
            await rig.session.capture_state(), harness.profile.engine, agent="fake", roots=harness.profile.state_paths
        )
        assert state.session_id == _sent(harness, "session/new")[-1].get("sessionId", state.session_id)
        assert state.session_id != "fake-session-1"
        assert [role for role, _ in state.recap] == ["user", "assistant", "user", "assistant"]
        await rig.session.close()

    async def test_wp91_acpcompact_4_no_session_yet_is_nothing_to_restart(self) -> None:
        rig = await open_rig(AcpHarness())
        session = rig.session
        assert isinstance(session, AcpSession)
        with pytest.raises(CompactFailed):
            _ = await session.restart_from_summary(EngineTask(turn_id_for(RUN, 0), "say one"))
        await session.close()


class TestOpencodeNotice:
    def test_wp91_acpcompact_5_the_notice_needs_opencodes_own_api(self) -> None:
        assert parse_profile(fake_entry(mcp=OPENCODE, compactionNotice="opencode")).compaction_notice == "opencode"
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(compactionNotice="opencode"))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp=OPENCODE, compactionNotice="gemini"))

    @pytest.fixture
    def summaries(self, monkeypatch: pytest.MonkeyPatch) -> list[list[AgentCompaction]]:
        """What opencode's loopback API lists, one entry per ask (the last repeats)."""
        answers: list[list[AgentCompaction]] = [[]]

        async def gateway(*_args: object, **_kwargs: object) -> None:
            return None

        async def listed(http: OpencodeHttp, session_id: str) -> tuple[AgentCompaction, ...]:
            assert session_id.startswith("fake-session-")
            return tuple(answers.pop(0) if len(answers) > 1 else answers[0])

        monkeypatch.setattr("harness_engines.acp.session.await_tool_gateway", gateway)
        monkeypatch.setattr("harness_engines.acp.session.opencode_summaries", listed)
        return answers

    async def test_wp91_acpcompact_6_a_summary_opencode_wrote_during_the_turn_is_one_compaction(
        self, summaries: list[list[AgentCompaction]]
    ) -> None:
        summary = AgentCompaction("msg-s1", 210_000, 900)
        summaries[:] = [[summary], [summary]]
        harness = AcpHarness(profile=parse_profile(fake_entry(mcp=OPENCODE, compactionNotice="opencode")))
        rig = await open_rig(harness)
        events = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say one"))
        (compacted,) = [e for e in events if isinstance(e, ContextCompacted)]
        assert (compacted.before_tokens, compacted.after_tokens) == (210_000, 900)
        assert events.index(compacted) < len(events) - 1 and isinstance(events[-1], TurnCompleted)
        # The same summary at the next turn's end is not a second compaction.
        again = await collect(rig.session, FollowUp(turn_id_for(RUN, 1), "say two"))
        assert not [e for e in again if isinstance(e, ContextCompacted)]
        await rig.session.close()

    async def test_wp91_acpcompact_7_a_loaded_session_counts_the_summaries_it_already_had(
        self, summaries: list[list[AgentCompaction]]
    ) -> None:
        old = AgentCompaction("msg-s0", 205_000, 800)
        profile = parse_profile(fake_entry(mcp=OPENCODE, compactionNotice="opencode"))
        harness = AcpHarness(profile=profile)
        summaries[:] = [[]]
        first = await open_rig(harness)
        _ = await collect(first.session, EngineTask(turn_id_for(RUN, 0), "say one"))
        state = await first.session.capture_state()
        await first.session.close()
        summaries[:] = [[old]]
        second = await open_rig(harness, restore=state, gateway=first.gateway)
        events = await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say two"))
        assert _sent(harness, "session/load")
        assert not [e for e in events if isinstance(e, ContextCompacted)]
        await second.session.close()

    async def test_wp91_acpcompact_8_a_profile_without_the_notice_never_asks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def unexpected(*_args: object, **_kwargs: object) -> tuple[AgentCompaction, ...]:
            raise AssertionError("asked an agent whose profile names no compaction notice")

        async def gateway(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr("harness_engines.acp.session.await_tool_gateway", gateway)
        monkeypatch.setattr("harness_engines.acp.session.opencode_summaries", unexpected)
        rig = await open_rig(AcpHarness(profile=parse_profile(fake_entry(mcp=OPENCODE))))
        assert isinstance((await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say one")))[-1], TurnCompleted)
        await rig.session.close()

"""WP-105 (C2.43): what the generic ACP driver needed for DeepSeek Harness (``dsh --profile acp``), all profile data.

Found on the real binary (0.2.2-rc.1, network none, a fake chat-completions endpoint):

- its ACP server answers ``initialize`` with ``agentInfo {deepseek-harness-acp, 0.0.1}`` whatever the release, so the
  profile names what it reports (``agent.reportedVersion``) beside the pinned release and digest;
- it offers ``sessionCapabilities.resume`` (``session/resume``: a session continued from its log, no replay) and no
  ``loadSession``, so ``resume.mode`` gains ``resume``;
- its ``session/prompt`` answer carries a stop reason only and ACP carries no compaction notice, but its session log
  (zstd JSON lines under its sessions root) records each model call's usage and each compaction it commits, so the
  driver reads it at each turn's end (``sessionRecord: dsh``);
- its tools come from plugin rows its patch switches on and off, so the launch's placeholders carry the run's mode as
  booleans (``${allows_<tool>}``, ``${denies_<tool>}``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.errors import EngineBinaryMismatch
from harness_core.engines.events import (
    ContextCompacted,
    TurnCompleted,
    UsageReported,
    turn_id_for,
)
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import EngineMode, EngineProfile, EngineTask, FollowUp
from harness_core.testing.engines.contract import (
    ENDPOINTS,
    collect,
    make_profile,
    open_rig,
)
from harness_engines.acp.compaction import AgentCompaction
from harness_engines.acp.dsh_record import (
    DSH_RECORD_SCRIPT,
    AgentRecord,
    CallUsage,
    parse_dsh_record,
)
from harness_engines.acp.launch import placeholders, render_launch
from harness_engines.acp.profiles import AcpProfileInvalid, parse_profile
from harness_engines.acp.testing.fake_agent import STORE, VERSION
from harness_engines.acp.testing.profile import FAKE_ENTRY, fake_entry, fake_profile
from tests.unit.harness_engines.acp.conftest import AcpHarness
from tests.unit.harness_engines.acp.test_e2e3_f6_gateway_probe import STATUS

if TYPE_CHECKING:
    from pathlib import Path

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import SandboxHandle

RUN = "run-wp105-dsh"
NODE = shutil.which("node")
RESUMING: dict[str, JsonValue] = {"mode": "resume", "paths": [STORE]}


def _methods(harness: AcpHarness) -> list[str]:
    connection = harness.fakes[harness.sandboxes[-1].id].connections[-1]
    return [str(m.get("method")) for m in connection.received if str(m.get("method", "")).startswith("session/")]


class TestReportedVersion:
    async def test_wp105_version_1_the_agent_may_report_a_fixed_version_the_profile_names(self) -> None:
        agent = dict(FAKE_ENTRY["agent"])  # type: ignore[arg-type]
        harness = AcpHarness(
            profile=fake_profile(agent={**agent, "reportedVersion": "0.0.1"}), behaviour={"agentVersion": "0.0.1"}
        )
        rig = await open_rig(harness)
        assert harness.profile.engine.version == VERSION and harness.profile.reported_version == "0.0.1"
        await rig.session.close()

    async def test_wp105_version_2_anything_else_is_still_a_mismatch(self) -> None:
        agent = dict(FAKE_ENTRY["agent"])  # type: ignore[arg-type]
        harness = AcpHarness(
            profile=fake_profile(agent={**agent, "reportedVersion": "0.0.1"}), behaviour={"agentVersion": VERSION}
        )
        with pytest.raises(EngineBinaryMismatch):
            _ = await open_rig(harness)


class TestResumeBySessionResume:
    async def test_wp105_resume_1_a_session_continues_by_session_resume_with_its_files_and_no_replay(self) -> None:
        harness = AcpHarness(profile=fake_profile(resume=RESUMING), behaviour={"loadSession": False, "resumeSession": True})
        first = await open_rig(harness)
        await collect(first.session, EngineTask(turn_id_for(RUN, 0), "say alpha\nRESUME-MARKER-105"))
        state = await first.session.capture_state()
        assert f"{STORE}/sessions/fake-session-1.json" in json.loads(state.payload)["files"]
        await first.session.close()
        second = await open_rig(harness, restore=state, gateway=first.gateway)
        events = await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say beta"))
        assert isinstance(events[-1], TurnCompleted) and events[-1].final_text == "beta"
        assert _methods(harness)[0] == "session/resume"
        assert "session/new" not in _methods(harness) and "session/load" not in _methods(harness)
        # The agent restored its own history: the earlier turn is in the model's context, not in a recap.
        users = [m.content for m in first.gateway.model_calls[-1].messages if m.role == "user"]
        assert any("RESUME-MARKER-105" in u for u in users)
        assert not any("continues an earlier one" in u for u in users)
        await second.session.close()

    async def test_wp105_resume_2_an_agent_that_does_not_advertise_resume_gets_a_new_session_and_the_recap(self) -> None:
        harness = AcpHarness(profile=fake_profile(resume=RESUMING), behaviour={"loadSession": True})
        first = await open_rig(harness)
        await collect(first.session, EngineTask(turn_id_for(RUN, 0), "say alpha\nRESUME-MARKER-106"))
        state = await first.session.capture_state()
        await first.session.close()
        second = await open_rig(harness, restore=state, gateway=first.gateway)
        assert isinstance((await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say beta")))[-1], TurnCompleted)
        assert "session/resume" not in _methods(harness) and "session/load" not in _methods(harness)
        assert any("continues an earlier one" in m.content for m in first.gateway.model_calls[-1].messages if m.role == "user")
        await second.session.close()

    def test_wp105_resume_3_resume_needs_the_folders_the_agent_keeps_its_sessions_in(self) -> None:
        assert parse_profile(fake_entry(resume=RESUMING)).resumes_from_files
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(resume={"mode": "resume"}))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(resume={"mode": "replay", "paths": [STORE]}))


class TestModeSwitches:
    @pytest.mark.parametrize(("mode", "shell", "edit"), [(EngineMode.BUILD, True, True), (EngineMode.PLAN, False, False)])
    def test_wp105_switch_1_the_runs_mode_is_a_boolean_per_native_tool(self, mode: EngineMode, shell: bool, edit: bool) -> None:
        agent = fake_profile()
        values = placeholders(agent, make_profile(AcpHarness(), mode=mode), ENDPOINTS, "/skills")
        assert (values["allows_shell"], values["denies_shell"]) == (shell, not shell)
        assert (values["allows_edit"], values["denies_edit"]) == (edit, not edit)
        assert values["allows_read"] is True
        # ACP carries no sub-agent, whatever the mode.
        assert values["allows_subagent"] is False and values["denies_subagent"] is True
        assert {f"allows_{t.value}" for t in NativeTool} <= set(values)

    def test_wp105_switch_2_a_template_takes_the_switch_as_a_boolean(self) -> None:
        agent = fake_profile(
            configs=[{"path": "/state/acp/fake/patch.yml", "template": [{"id": "tool-bash", "disabled": "${denies_shell}"}]}]
        )
        profile = EngineProfile(engine=agent.engine, mode=EngineMode.PLAN, access=AccessRouteRef(route_id="r", digest="d"))
        launch = render_launch(agent, profile, ENDPOINTS, "/skills")
        assert json.loads(launch.files["/state/acp/fake/patch.yml"]) == [{"id": "tool-bash", "disabled": True}]


class TestDshRecord:
    def test_wp105_rec_1_calls_and_committed_compactions_are_read_in_order(self) -> None:
        printed = "\n".join(
            (
                json.dumps({"k": "call", "id": "m1", "in": 100, "out": 7, "cr": 0}),
                json.dumps({"k": "call", "id": "compaction:c1", "in": 900, "out": 40, "cr": 10}),
                json.dumps({"k": "compaction", "id": "c1", "before": 800, "after": 40}),
                "not json",
                json.dumps({"k": "call", "id": "", "in": 1, "out": 1}),
                json.dumps({"k": "seq", "seq": 41}),
            )
        )
        assert parse_dsh_record(printed) == AgentRecord(
            (CallUsage("m1", 100, 7), CallUsage("compaction:c1", 900, 40, 10)), (AgentCompaction("c1", 800, 40),), 41
        )

    def test_wp105_rec_2_the_record_is_read_with_the_agents_runtime_under_its_session_folder(self) -> None:
        assert parse_profile(fake_entry(mcp=STATUS, sessionRecord="dsh", resume=RESUMING)).session_record == "dsh"
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(sessionRecord="dsh", resume=RESUMING))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp=STATUS, sessionRecord="dsh", resume={"mode": "recap"}))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp=STATUS, sessionRecord="dsh", resume=RESUMING, compactionNotice="qwen"))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp=STATUS, sessionRecord="other", resume=RESUMING))


@pytest.mark.skipif(NODE is None, reason="needs Node 22.15 or later on PATH (the record script is the agent runtime's)")
class TestDshRecordScript:
    @staticmethod
    def _log(tmp_path: Path, session: str, events: list[dict[str, JsonValue]], *, frames: int = 2) -> Path:
        """dsh's log as it writes it: JSON lines, appended as separate zstd frames."""
        assert NODE is not None
        folder = tmp_path / "--workspace--" / session
        folder.mkdir(parents=True)
        lines = [json.dumps(e) + "\n" for e in events]
        size = max(1, len(lines) // frames)
        chunks = ["".join(lines[i : i + size]) for i in range(0, len(lines), size)]
        script = (
            "const fs=require('fs'),z=require('zlib');"
            "const [out,...parts]=process.argv.slice(1);"
            "fs.writeFileSync(out,Buffer.concat(parts.map(p=>z.zstdCompressSync(Buffer.from(p)))));"
        )
        target = folder / "session.v4.jsonl.zstd"
        _ = subprocess.run([NODE, "-e", script, str(target), *chunks], check=True)
        return target

    def _run(self, root: Path, session: str, after: int = -1) -> subprocess.CompletedProcess[str]:
        assert NODE is not None
        return subprocess.run(
            [NODE, "-e", DSH_RECORD_SCRIPT, str(root), session, str(after)], capture_output=True, text=True, check=False
        )

    def test_wp105_rec_3_the_script_prints_usage_and_compactions_and_nothing_of_the_conversation(self, tmp_path: Path) -> None:
        events: list[dict[str, JsonValue]] = [
            {"type": "session", "version": 4, "id": "s-1"},
            {"type": "user/message", "seq": 1, "data": {"content": [{"type": "text", "text": "secret words"}]}},
            {"type": "assistant/message", "seq": 2, "data": {"message": {"id": "m1", "content": [{"type": "text", "text": "secret"}]},
                                                             "usage": {"inputTokens": 100, "outputTokens": 7, "totalTokens": 107}}},
            {"type": "compaction/start", "seq": 3, "data": {"compactionId": "c1"}},
            {"type": "compaction/summary", "seq": 4, "data": {"compactionId": "c1", "summary": "secret summary",
                                                              "shadowedTokenCount": 800, "usage": {"inputTokens": 900, "outputTokens": 40}}},
            {"type": "compaction/end", "seq": 5, "data": {"compactionId": "c1"}},
            {"type": "compaction/summary", "seq": 6, "data": {"compactionId": "c2", "shadowedTokenCount": 5}},
            {"type": "compaction/end", "seq": 7, "data": {"compactionId": "c2", "error": [{"message": "failed"}]}},
            {"type": "assistant/message", "seq": 8, "data": {"message": {"id": "m2"}, "usage": {"inputTokens": 120, "outputTokens": 9}}},
        ]
        _ = self._log(tmp_path, "s-1", events, frames=3)
        done = self._run(tmp_path, "s-1")
        assert done.returncode == 0, done.stderr
        assert "secret" not in done.stdout
        assert parse_dsh_record(done.stdout) == AgentRecord(
            (CallUsage("m1", 100, 7), CallUsage("compaction:c1", 900, 40), CallUsage("m2", 120, 9)),
            (AgentCompaction("c1", 800, 40),),
            8,
        )
        # Read on from an earlier turn's end: only what came after it.
        assert parse_dsh_record(self._run(tmp_path, "s-1", after=5).stdout).calls == (CallUsage("m2", 120, 9),)

    def test_wp105_rec_4_no_log_yet_is_nothing(self, tmp_path: Path) -> None:
        done = self._run(tmp_path / "none", "s-1")
        assert done.returncode == 0
        assert parse_dsh_record(done.stdout) == AgentRecord()


class TestDshRecordInTheTurn:
    @pytest.fixture
    def records(self, monkeypatch: pytest.MonkeyPatch) -> list[AgentRecord]:
        """What the log adds at each read (the last repeats)."""
        answers: list[AgentRecord] = [AgentRecord()]
        seen: list[int] = []

        async def gateway(*_args: object, **_kwargs: object) -> None:
            return None

        async def read(_sandbox: SandboxHandle, *, runtime: str, root: str, session_id: str, after_seq: int) -> AgentRecord:
            assert runtime == STATUS["status"]["runtime"]  # type: ignore[index]
            assert root == STORE and session_id.startswith("fake-session-")
            seen.append(after_seq)
            return answers.pop(0) if len(answers) > 1 else answers[0]

        monkeypatch.setattr("harness_engines.acp.session.await_gateway_tools", gateway)
        monkeypatch.setattr("harness_engines.acp.session.dsh_record", read)
        self.after = seen
        return answers

    async def test_wp105_rec_5_the_turns_usage_is_its_new_calls_and_a_new_compaction_is_one_event(
        self, records: list[AgentRecord]
    ) -> None:
        records[:] = [
            AgentRecord((CallUsage("m1", 100, 7), CallUsage("m2", 50, 3, 20)), (AgentCompaction("c1", 800, 40),), 9),
            AgentRecord((CallUsage("m2", 50, 3, 20), CallUsage("m3", 70, 5)), (), 14),
        ]
        entry = fake_entry(mcp=STATUS, sessionRecord="dsh", resume=RESUMING)
        rig = await open_rig(AcpHarness(profile=parse_profile(entry), behaviour={"promptUsage": False}))
        events = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say one"))
        (usage,) = [e for e in events if isinstance(e, UsageReported)]
        assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (150, 10, 20)
        (compacted,) = [e for e in events if isinstance(e, ContextCompacted)]
        assert (compacted.before_tokens, compacted.after_tokens) == (800, 40)
        again = await collect(rig.session, FollowUp(turn_id_for(RUN, 1), "say two"))
        (usage,) = [e for e in again if isinstance(e, UsageReported)]
        # m2 was the first turn's: a call is counted once whatever the read returns.
        assert (usage.input_tokens, usage.output_tokens) == (70, 5)
        assert not [e for e in again if isinstance(e, ContextCompacted)]
        assert self.after == [-1, 9]
        await rig.session.close()

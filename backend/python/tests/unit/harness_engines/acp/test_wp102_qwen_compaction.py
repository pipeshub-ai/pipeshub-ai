"""WP-102 (C2.41, MG-10): Qwen Code's own compaction, learnt from its session record.

Found on the real binary (CF-21): Qwen Code 0.26.5 answers a context overflow by compressing the session and retrying
by itself, and says nothing over ACP the driver can read (at most a free-text diagnostic). It records the compression
in the session's own record, a JSON-lines file under its runtime folder
(``<QWEN_RUNTIME_DIR>/projects/-workspace/chats/<session id>.jsonl``), as a ``system`` line of subtype
``chat_compression`` whose ``info`` holds the token counts before and after and the outcome (``compressionStatus`` 1
is compressed). A profile that says ``compactionNotice: qwen`` has the driver read those lines at each turn's end,
inside the sandbox with the agent's own runtime, and tell each new one as a ``ContextCompacted``, as opencode's
loopback summaries are told (WP-91).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.events import ContextCompacted, TurnCompleted, turn_id_for
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.testing.engines.contract import collect, open_rig
from harness_engines.acp.compaction import (
    QWEN_RECORDS_SCRIPT,
    AgentCompaction,
    parse_qwen_records,
    qwen_records_path,
)
from harness_engines.acp.profiles import AcpProfileInvalid, parse_profile
from harness_engines.acp.testing.profile import fake_entry
from tests.unit.harness_engines.acp.conftest import AcpHarness
from tests.unit.harness_engines.acp.test_e2e3_f6_gateway_probe import STATUS

if TYPE_CHECKING:
    from pathlib import Path

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import SandboxHandle

RUN = "run-wp102-qwen-compaction"
NODE = shutil.which("node")


def _record(uuid: str, status: int, before: int, after: int) -> str:
    info = {"originalTokenCount": before, "newTokenCount": after, "compressionStatus": status, "triggerReason": "auto"}
    return json.dumps({"uuid": uuid, "type": "system", "subtype": "chat_compression", "systemPayload": {"info": info}})


class TestQwenRecords:
    def test_wp102_qwencompact_1_the_record_is_the_sessions_own_file(self) -> None:
        assert qwen_records_path("/state/qwen", "3daf9b6b-2b85") == "/state/qwen/projects/-workspace/chats/3daf9b6b-2b85.jsonl"
        # A session id is the agent's: one that would leave the folder names no file.
        assert qwen_records_path("/state/qwen", "../../etc/passwd") is None

    def test_wp102_qwencompact_2_only_a_completed_compression_is_one(self) -> None:
        printed = "\n".join(
            (
                json.dumps({"id": "r1", "status": 1, "before": 210_000, "after": 900}),
                json.dumps({"id": "r2", "status": 5, "before": 10, "after": 10}),
                "not json",
                json.dumps({"id": "", "status": 1, "before": 1, "after": 1}),
            )
        )
        assert parse_qwen_records(printed) == (AgentCompaction("r1", 210_000, 900),)

    def test_wp102_qwencompact_3_the_notice_needs_a_runtime_and_a_state_folder(self) -> None:
        assert parse_profile(fake_entry(mcp=STATUS, compactionNotice="qwen")).compaction_notice == "qwen"
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(compactionNotice="qwen"))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp=STATUS, compactionNotice="qwen", resume={"mode": "recap"}))


@pytest.mark.skipif(NODE is None, reason="needs Node on PATH (the record script is the agent runtime's JavaScript)")
class TestQwenRecordScript:
    def test_wp102_qwencompact_5_the_script_prints_each_compression_and_nothing_of_the_history(self, tmp_path: Path) -> None:
        """The script on the host's Node, over a record in Qwen's shape: the user's line and the compressed history
        never come out, a missing file is nothing."""
        assert NODE is not None
        record = tmp_path / "s1.jsonl"
        compressed = json.loads(_record("r1", 1, 210_000, 900))
        compressed["systemPayload"]["compressedHistory"] = [{"role": "user", "parts": [{"text": "secret words"}]}]
        lines = (
            json.dumps({"uuid": "u0", "type": "user", "message": {"parts": [{"text": "mentions chat_compression"}]}}),
            json.dumps(compressed),
            _record("r2", 5, 10, 10),
        )
        _ = record.write_text("\n".join(lines) + "\n")
        printed = subprocess.run([NODE, "-e", QWEN_RECORDS_SCRIPT, str(record)], capture_output=True, text=True, check=True).stdout
        assert "secret" not in printed
        assert parse_qwen_records(printed) == (AgentCompaction("r1", 210_000, 900),)
        missing = subprocess.run([NODE, "-e", QWEN_RECORDS_SCRIPT, str(tmp_path / "none.jsonl")], capture_output=True, check=False)
        assert (missing.returncode, missing.stdout) == (0, b"")


class TestQwenNotice:
    @pytest.fixture
    def records(self, monkeypatch: pytest.MonkeyPatch) -> list[list[AgentCompaction]]:
        """What the session record holds at each turn's end (the last repeats)."""
        answers: list[list[AgentCompaction]] = [[]]

        async def gateway(*_args: object, **_kwargs: object) -> None:
            return None

        async def read(_sandbox: SandboxHandle, *, runtime: str, path: str) -> tuple[AgentCompaction, ...]:
            assert runtime == STATUS["status"]["runtime"]  # type: ignore[index]
            assert path.endswith(".jsonl") and "/projects/-workspace/chats/fake-session-" in path
            return tuple(answers.pop(0) if len(answers) > 1 else answers[0])

        monkeypatch.setattr("harness_engines.acp.session.await_gateway_tools", gateway)
        monkeypatch.setattr("harness_engines.acp.session.qwen_summaries", read)
        return answers

    async def test_wp102_qwencompact_4_a_compression_qwen_recorded_during_the_turn_is_one_compaction(
        self, records: list[list[AgentCompaction]]
    ) -> None:
        summary = AgentCompaction("r1", 210_000, 900)
        records[:] = [[summary], [summary]]
        entry: dict[str, JsonValue] = fake_entry(mcp=STATUS, compactionNotice="qwen")
        rig = await open_rig(AcpHarness(profile=parse_profile(entry)))
        events = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say one"))
        (compacted,) = [e for e in events if isinstance(e, ContextCompacted)]
        assert (compacted.before_tokens, compacted.after_tokens) == (210_000, 900)
        assert isinstance(events[-1], TurnCompleted)
        again = await collect(rig.session, FollowUp(turn_id_for(RUN, 1), "say two"))
        assert not [e for e in again if isinstance(e, ContextCompacted)]
        await rig.session.close()

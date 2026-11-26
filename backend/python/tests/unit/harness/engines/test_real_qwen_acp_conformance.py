"""RX-QWEN / ACP-QWEN (C2.41): ``EngineAdapterContract`` on Qwen Code's ACP mode behind the real gateways.

The ACP driver runs the shipped ``acp-qwen`` profile (``harness_engines/acp_agents.json``): Qwen Code 0.26.5's own ACP
mode (``qwen --acp``, launched as its pinned entry point ``cli.js``) in its image
(``deployment/sandbox-images/acp-qwen``), on the model gateway's chat-completions wire through its OpenAI auth type,
with the tool gateway declared in the sealed system settings alone (``acp_chat_lane.py``). Skipped without Docker
and the image (``deployment/sandbox-images/acp-qwen/build.sh``).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from harness_core.engines.events import (
    CommandOutput,
    CommandStarted,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.types import EngineTask
from tests.unit.harness.engines.acp_chat_lane import AcpChatLane
from tests.unit.harness.engines.lane_releases import lane_acp_profile

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]


class TestAcpQwenOnTheRealBinary(AcpChatLane):
    # Qwen Code 0.26.5 reads the workspace's AGENTS.md (D2); its repository skills are off (skills.disabledLevels
    # project in the run's user settings, UNION-merged, ADR-ENG-01 D1)
    repository_context_files = frozenset({"AGENTS.md"})
    repository_skills = frozenset()
    PROFILE = lane_acp_profile("qwen")
    BUILD_SCRIPT = "deployment/sandbox-images/acp-qwen/build.sh"
    PLAN_HIDDEN = frozenset({"edit", "write_file", "run_shell_command", "agent", "web_fetch", "web_search"})
    PLAN_SHOWN = frozenset({"read_file", "glob"})

    # R31JFIX-FU-2: about one command in thirty-five reports no output (5 of 180 on the real binary under gVisor); 40
    # commands find that at least once in about two runs of three.
    COMMANDS: ClassVar[int] = 40

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "upstream, Qwen Code 0.26.5: with no PTY backend in the image its shell runs on child_process pipes, and "
            "ShellExecutionService.childProcessFallback settles a command on the child's 'exit' event, which Node may "
            "emit before the pipes' last 'data' (it waits for 'close' only when streaming or capturing), so a short "
            "command's whole output is sometimes lost: its own rawOutput says output '', exit 0 (ACP-driver.md, Qwen Code)"
        ),
    )
    async def test_r31jfix_fu2_every_command_reports_its_output(self, engine_harness: Any, rigs: Any) -> None:  # noqa: ANN401
        rig = await self._open(engine_harness, rigs)
        prompt = "\n".join([*(f"shell printf qwen-out-{n}" for n in range(self.COMMANDS)), "say done"])
        events = await self.collect(rig.session, EngineTask(turn_id=turn_id_for("fu2", 0), prompt=prompt))
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        started = {e.call_id: e.command for e in events if isinstance(e, CommandStarted)}
        shown = {e.call_id: e.text for e in events if isinstance(e, CommandOutput)}
        assert len(started) == self.COMMANDS
        empty = [command for call, command in started.items() if not shown.get(call)]
        assert not empty, f"{len(empty)} of {len(started)} commands reported no output: {empty}"
        assert sorted(shown.values()) == sorted(f"qwen-out-{n}" for n in range(self.COMMANDS))

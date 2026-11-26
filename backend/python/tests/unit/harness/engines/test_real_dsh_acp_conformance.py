"""RX-DSH / ACP-DSH (C2.43, WP-105): ``EngineAdapterContract`` on DeepSeek Harness's ACP server behind the real gateways.

The ACP driver runs the shipped ``acp-dsh`` profile (``harness_engines/acp_agents.json``): DeepSeek Harness
0.2.2-rc.1's own ACP profile (``dsh --profile acp``, launched as its pinned ``bin.js``) in its image
(``deployment/sandbox-images/acp-dsh``), on the model gateway's chat-completions wire through pi-ai's
openai-completions route, with the tool gateway sent in ``session/new`` and ``session/resume`` (``acp_chat_lane.py``).
Skipped without Docker and the image (``deployment/sandbox-images/acp-dsh/build.sh``).
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_core.engines.events import TurnCompleted, turn_id_for
from harness_core.engines.types import EngineTask
from tests.unit.harness.engines.acp_chat_lane import AcpChatLane
from tests.unit.harness.engines.lane_releases import lane_acp_profile

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]


class TestAcpDshOnTheRealBinary(AcpChatLane):
    PROFILE = lane_acp_profile("dsh")
    BUILD_SCRIPT = "deployment/sandbox-images/acp-dsh/build.sh"
    PLAN_HIDDEN = frozenset({"write", "edit", "bash", "subagent", "subagent_fork", "web_fetch", "workflow", "create_goal"})
    PLAN_SHOWN = frozenset({"read", "glob", "grep"})

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "upstream, dsh 0.2.2-rc.1: its agent-instructions plugin renders the shared agents home's AGENTS.md (where "
            "the run's instructions are; no project file, CF-02) as a user-role <system-reminder> in the first request, never "
            "in the system prompt, which is assembled from plugin sections and a static persona; the instructions still "
            "reach the model exactly once (test_wp105_the_runs_instructions_reach_the_model_once_as_dshs_agents_md)"
        ),
    )
    async def test_cf2_instructions_reach_the_model_once(self, engine_harness: Any, rigs: Any) -> None:  # noqa: ANN401
        await super().test_cf2_instructions_reach_the_model_once(engine_harness, rigs)

    async def test_wp105_the_runs_instructions_reach_the_model_once_as_dshs_agents_md(
        self, engine_harness: Any, rigs: Any  # noqa: ANN401
    ) -> None:
        """CF-02 as dsh carries it: the run's instructions file is its shared agents home's AGENTS.md, read into one
        user-role reminder of the first request, and nowhere else."""
        marker = "INSTRUCTIONS-MARKER-dsh-105"
        rig = await self._open(engine_harness, rigs)
        task = EngineTask(turn_id=turn_id_for("conformance", 0), prompt="say hi", instructions=f"House style. {marker}")
        events = await self.collect(rig.session, task)
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        first = rig.gateway.model_calls[0]
        texts = [m.content for m in first.messages if m.role == "user"]
        assert sum(t.count(marker) for t in texts) == 1, texts
        assert "$DSH_AGENTS_HOME/AGENTS.md" in next(t for t in texts if marker in t)

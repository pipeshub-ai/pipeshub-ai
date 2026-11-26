"""RX-GOOSE / ACP-GOOSE (C2.41): ``EngineAdapterContract`` on Goose's ACP mode behind the real gateways.

The ACP driver runs the shipped ``acp-goose`` profile (``harness_engines/acp_agents.json``): Goose 1.57.0's own ACP
mode (``goose acp``) in its image (``deployment/sandbox-images/acp-goose``), on the model gateway's chat-completions
wire through Goose's OpenAI provider, with the tool gateway declared in its rendered ``config.yaml`` alone
(``acp_chat_lane.py``). Skipped without Docker and the image (``deployment/sandbox-images/acp-goose/build.sh``).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.harness.engines.acp_chat_lane import AcpChatLane
from tests.unit.harness.engines.lane_releases import lane_acp_profile

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]




class TestAcpGooseOnTheRealBinary(AcpChatLane):
    # Goose 1.57.0 reads .goosehints and AGENTS.md between the git root and its folder into its Project Hints (D2) and
    # lists the workspace's .agents/skills, with no setting for either, so its skills are lower-trust content (ADR-
    # ENG-01 D1, R31ENG-FU-1 upstream)
    repository_context_files = frozenset({".goosehints", "AGENTS.md"})
    repository_skills = frozenset({"skill:.agents"})
    PROFILE = lane_acp_profile("goose")
    BUILD_SCRIPT = "deployment/sandbox-images/acp-goose/build.sh"
    PLAN_HIDDEN = frozenset({"write", "edit", "shell", "delegate", "subagent", "manage_extensions"})
    PLAN_SHOWN = frozenset({"tree"})
    MODEL_REQUESTS = frozenset({("POST", "/v1/chat/completions"), ("GET", "/v1/models")})
    EXACT_BUDGET_CODE = False

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "MG-10 open: Goose over ACP compacts and retries by itself, but ACP carries no compaction notice, so the "
            "chat gets no ContextCompacted and sees Goose's summary as an extra message"
        ),
    )
    async def test_cf21_mg10_an_overflow_is_compacted_then_retried(
        self, engine_harness: Any, rigs: Any  # noqa: ANN401
    ) -> None:
        await super().test_cf21_mg10_an_overflow_is_compacted_then_retried(engine_harness, rigs)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "CF-20 deviation accepted as upstream: Goose NFC-normalises its output "
            "(sanitize_unicode_tags, not configurable); Goose is never the org default and admins see a note"
        ),
    )
    async def test_spec_cf20_non_ascii_rtl_and_emoji_round_trip_byte_exact(
        self, engine_harness: Any, rigs: Any  # noqa: ANN401
    ) -> None:
        await super().test_spec_cf20_non_ascii_rtl_and_emoji_round_trip_byte_exact(engine_harness, rigs)

"""WP70-D1: a manual deep-research run's shape on the real lane: research cut at its step allowance still delivers report.md.

A real engine (opencode by default; ``E1_ENGINE=codex`` for Codex) in the Docker broker's sandbox behind the egress
proxy and the tool gateway, on the lane's scripted model. It writes a placeholder report.md, reads pages through the
gateway, keeps a note, and is still reading when its 14-step allowance ends. The turn is told to stop gathering at its
writing reserve (its later calls are refused), the limit cuts it, and the run's wrap-up (a scripted model over the real
prompt) writes report.md from what was gathered, cited, over the placeholder.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING

import pytest

from tests.unit.harness.engines.durable_stack import durable_lane
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]
ENGINE = os.environ.get("E1_ENGINE", "opencode")
STEPS = 14
REQUEST = (
    "Research how Onyx compares with PipesHub. Cite every finding with its source URL. Write the final report as one "
    "Markdown file named report.md in your output folder."
)


def _lookup(n: int) -> str:
    return "tool lookup " + json.dumps({"url": f"https://docs.onyx.app/page-{n}"})


SCRIPT = "\n".join([
    REQUEST,
    "write out/report.md Research in progress.",
    *[_lookup(n) for n in range(3)],
    "write notes/progress.md Onyx lists its connectors.",
    *[_lookup(n) for n in range(3, 24)],
    "say I'm focusing on demonstrated differences.",
])


@pytest.fixture(autouse=True)
def _available() -> None:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)


async def test_wp70_d1_research_cut_at_its_step_allowance_still_delivers_a_cited_report(tmp_path: Path) -> None:
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        await lane.admit("run-deep", SCRIPT, limits={"max_steps": STEPS}, worker={"wrap_up": True})
        worker = await lane.worker("run-deep")
        result = await worker.wait(600)
        log = worker.log()
        assert result is not None and result["status"] == "completed", (result, log[-4000:])
        events = await lane.events("run-deep")
        (completed,) = [e["data"] for e in events if e["type"] == "run.completed"]
        assert completed == {"outcome": "answered", "truncatedBy": "turns"}, log[-4000:]
        assert "engine.turn.synthesis" in log, "the engine was told to stop gathering before its limit"
        assert "wrappedUp=True" in log and "saved=True" in log, log[-4000:]
        looked_up = sum(1 for e in events if e["type"] == "tool.outcome")
        assert 0 < looked_up < 24, "the reads after the writing reserve opened never ran"
        versions = lane.artifacts(lane.sessions["run-deep"])["report.md"]
        report = versions[-1].decode("utf-8")
        assert len(versions) >= 2 and "Research in progress" not in report, report
        cited = set(re.findall(r"\(https://docs\.onyx\.app/page-\d+\)", report))
        assert cited and len(cited) <= looked_up, report

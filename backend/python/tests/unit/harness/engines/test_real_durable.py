"""RL-1..RL-3 (E1): a durable engine run end to end on the real lane.

Node's journal server, the real egress proxy (tokens from Node), the real opencode image under runc, a
worker process running ``RunDriver`` with the ``EngineKernel`` and its own tool gateway (``durable_stack``).
``E1_ENGINE=claude_code`` (E9 DR-1) or ``codex`` runs the same cases on that engine's image.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from tests.unit.harness.engines.durable_stack import durable_lane
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]
ENGINE = os.environ.get("E1_ENGINE", "opencode")

RL1 = """RL1
write out/report.md quarterly numbers
tool create {"title": "rl1"}
tool lookup {"q": "churn"}
say all done"""
RL2 = """RL2
tool create {"title": "same"}
tool create {"title": "same"}
say twice"""


@pytest.fixture(autouse=True)
def _available() -> None:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)


async def test_rl1_an_engine_run_completes_through_node_with_one_effect_and_its_artifact(tmp_path: Path) -> None:
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        await lane.admit("run-rl1", RL1)
        worker = await lane.worker("run-rl1")
        result = await worker.wait(600)
        assert result is not None and result["status"] == "completed", (result, worker.log()[-4000:])
        events = await lane.events("run-rl1")
        types = Counter(e["type"] for e in events)
        assert (await lane.run("run-rl1"))["status"] == "completed"
        assert types["turn.started"] == types["turn.completed"] == 1
        assert types["engine.checkpointed"] == 1
        assert {e["data"]["engine"]["kind"] for e in events if e["type"] == "engine.checkpointed"} == {ENGINE}
        assert types["workspace.committed"] >= 1
        assert types["run.completed"] == 1
        assert types["x.pipeshub.engine.progress"] >= 2
        assert types["x.pipeshub.answer.finalized"] == 1
        assert types["x.pipeshub.artifact.published"] == 1
        assert lane.saas.effects.total() == 1
        assert lane.sandboxes("run-rl1") == []


async def test_rl2_an_identical_effectful_call_runs_once_then_is_recorded(tmp_path: Path) -> None:
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        await lane.admit("run-rl2", RL2)
        worker = await lane.worker("run-rl2")
        result = await worker.wait(600)
        assert result is not None and result["status"] == "completed", (result, worker.log()[-4000:])
        assert list(lane.saas.effects.values()) == [1]
        # C13-2 on the durable journal: one intent and one outcome; the repeat is answered from the record.
        types = Counter(e["type"] for e in await lane.events("run-rl2"))
        assert (types["tool.intent"], types["tool.outcome"]) == (1, 1)
        results = [m.content for m in lane.model.model_calls[-1].messages if m.role == "tool"]
        assert sum("created T-1" in r for r in results) == 2, results


async def test_rl3_a_route_the_guard_refuses_fails_the_run_with_its_code(tmp_path: Path) -> None:
    """A1 on the durable path: another user's key pinned on the route; the gateway's guard refuses every
    call before the provider, and the engine run fails with the guard's code (S1), nothing executed."""
    other = {
        "provider": "openai",
        "model": "gpt-x",
        "access": {"route": "gateway", "accountKind": "user_api_key", "accountId": "users/user-2/openAI", "pays": "user"},
    }
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        await lane.admit("run-rl3", RL1, route=other)
        worker = await lane.worker("run-rl3")
        result = await worker.wait(600)
        assert result is not None and (result["status"], result["reason"]) == ("failed", "account_not_owned"), (
            result,
            worker.log()[-4000:],
        )
        assert lane.model.model_calls == []
        assert lane.saas.effects.total() == 0



B1 = """B1
todo [{"title": "Find the invoices", "status": "in_progress"}, {"title": "Draft the review", "status": "pending"}]
todo [{"title": "Find the invoices", "status": "completed"}, {"title": "Draft the review", "status": "in_progress"}]
say planned"""


async def test_b1_22_an_engines_own_task_list_is_the_runs_plan_and_has_no_effect(tmp_path: Path) -> None:
    """UXR-B1 on the real lane: the engine's task-list tool, through the real binary and Node's journal, is the
    run's ``task.*`` plan; and calling it changes nothing: no file in the workspace, no gateway call, no grant or
    approval. Codex 0.160 is given ``update_plan`` by ``[tools.update_plan]`` (FIX-P2 C3)."""
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        await lane.admit("run-b1", B1)
        worker = await lane.worker("run-b1")
        result = await worker.wait(600)
        assert result is not None and result["status"] == "completed", (result, worker.log()[-4000:])
        events = await lane.events("run-b1")
        tasks = [
            (e["type"], e["data"]["taskId"], e["data"]["title"], e["data"]["status"])
            for e in events
            if e["type"].startswith("task.")
        ]
        assert tasks == [
            ("task.created", "task-1", "Find the invoices", "in_progress"),
            ("task.created", "task-2", "Draft the review", "pending"),
            ("task.updated", "task-1", "Find the invoices", "completed"),
            ("task.updated", "task-2", "Draft the review", "in_progress"),
        ], [c.tools for c in lane.model.model_calls]
        types = Counter(e["type"] for e in events)
        assert all(not e["data"].get("changed") for e in events if e["type"] == "workspace.committed")
        assert (types["tool.intent"], types["interaction.requested"], types["x.pipeshub.artifact.published"]) == (0, 0, 0)
        assert lane.saas.effects.total() == 0

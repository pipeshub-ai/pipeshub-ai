"""K-2 and K-4 with a real SIGKILL: the worker process dies at the crash point."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from harness_core.failpoints import Kill9
from harness_core.kernel.core.messages import AssistantMessage, ThinkingPart
from harness_core.testing import write_arms

from .scenarios import SCENARIOS
from .support import RUN, World

CHILD = Path(__file__).with_name("crash_child.py")
PYTHON_ROOT = Path(__file__).resolve().parents[4]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL is POSIX-only")


def _kill_child(root: Path, scenario: str) -> World:
    World(root=root).admit()
    arms = root / "failpoints.json"
    write_arms(arms, {SCENARIOS[scenario].failpoint: Kill9()})
    result = subprocess.run(
        [sys.executable, str(CHILD), str(root), scenario, str(arms)],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stdout + result.stderr
    world = World(root=root)
    world.expire()
    return world


async def test_k2_kill9_after_response_commit_resumes_without_calling_the_model(
    tmp_path: Path,
) -> None:
    world = _kill_child(tmp_path, "k2")
    assert [e.type for e in world.events("model.response")] == ["model.response"]
    model, port = SCENARIOS["k2"].resume()
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert port.calls == [] and model.remaining == 0
    final = done.transcript[-1]
    assert isinstance(final, AssistantMessage) and final.text == "the answer"
    assert any(isinstance(p, ThinkingPart) for p in final.content)


async def test_k4_kill9_after_effect_idempotent_retry_is_deduped(
    tmp_path: Path,
) -> None:
    world = _kill_child(tmp_path, "k4-idempotent")
    assert len(world.saas.effects) == 1 and world.events("tool.outcome") == []
    _, port = SCENARIOS["k4-idempotent"].resume()
    assert (await world.run(world.kernel(port))).status == "completed"
    world.saas.assert_exactly_once(f"org1:{RUN}:t1.0")
    assert World(root=tmp_path).saas.effects == world.saas.effects


async def test_k4_kill9_after_effect_non_idempotent_is_unknown(tmp_path: Path) -> None:
    world = _kill_child(tmp_path, "k4-non-idempotent")
    assert len(world.saas.effects) == 1
    _, port = SCENARIOS["k4-non-idempotent"].resume()
    suspended = await world.run(world.kernel(port))
    assert suspended.status == "suspended" and suspended.reason == "ix.unknown.t1.0"
    assert len(world.saas.effects) == 1
    assert world.events("tool.unknown")

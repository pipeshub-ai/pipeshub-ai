"""GV-1..GV-5: coding engines need gVisor on a production install.

The release profile's floor is gVisor. A production install (the override unset) whose broker offers only hardened
runc refuses the run before any sandbox, with ``gvisor_unavailable`` and an admin's sentence naming the install guide.
The documented dev/test override lowers the release floor to hardened runc. With ``runsc`` offered, gVisor is used,
override or not (the controller never takes a weaker runtime than it is offered at or above the floor).
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

from harness_core.engines.isolation import (
    GVISOR_REQUIRED_MESSAGE,
    GVISOR_UNAVAILABLE,
    INSTALL_GUIDE,
    OVERRIDE_ENV,
    isolation_refusal,
    override_on,
    release_floor,
)
from harness_core.sandbox.errors import IsolationUnavailable
from harness_core.sandbox.types import RuntimeMode
from harness_engines.release import engines_without_gvisor, release_default_wire
from tests.unit.harness_core.engines.test_controller import World, task

HARDENED = frozenset({RuntimeMode.DOCKER_HARDENED})
BOTH = frozenset({RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR})


def _release_floor_run(world: World, monkeypatch: pytest.MonkeyPatch, *, override: bool):  # noqa: ANN202
    if override:
        monkeypatch.setenv(OVERRIDE_ENV, "true")
    else:
        monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    floor = RuntimeMode(str(release_default_wire()["isolationMin"]))
    run = world.run()
    return dataclasses.replace(run, profile=dataclasses.replace(run.profile, isolation_min=floor))


async def test_gv1_production_without_runsc_refuses_the_engine_with_the_install_guide(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    world = World(modes=HARDENED)
    run = _release_floor_run(world, monkeypatch, override=False)
    with caplog.at_level(logging.WARNING), pytest.raises(IsolationUnavailable) as refused:
        await world.controller.run_turn(run, task(0, "say ok"))
    assert refused.value.reason == GVISOR_UNAVAILABLE
    assert str(refused.value) == GVISOR_REQUIRED_MESSAGE
    assert str(refused.value).startswith("Coding engines need the gVisor sandbox runtime on this server — install guide: ")
    assert INSTALL_GUIDE.startswith("https://") and INSTALL_GUIDE in str(refused.value)
    assert world.broker.acquired == [] and world.host.mounted == 0
    assert [r.getMessage() for r in caplog.records if "engine.run.refused" in r.getMessage()]


async def test_gv2_the_dev_override_runs_the_engine_on_hardened_runc(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(modes=HARDENED)
    run = _release_floor_run(world, monkeypatch, override=True)
    outcome = await world.controller.run_turn(run, task(0, "say ok"))
    assert outcome.reason == "completed"
    [(spec, _)] = world.broker.acquired
    assert spec.mode is RuntimeMode.DOCKER_HARDENED


@pytest.mark.parametrize("override", [False, True], ids=["production", "override"])
async def test_gv3_with_runsc_offered_gvisor_is_used(monkeypatch: pytest.MonkeyPatch, *, override: bool) -> None:
    world = World(modes=BOTH)
    run = _release_floor_run(world, monkeypatch, override=override)
    outcome = await world.controller.run_turn(run, task(0, "say ok"))
    assert outcome.reason == "completed"
    [(spec, _)] = world.broker.acquired
    assert spec.mode is RuntimeMode.GVISOR


@pytest.mark.parametrize(
    ("value", "on"),
    [(None, False), ("", False), ("false", False), ("1", False), ("yes", False), ("true", True), (" TRUE ", True)],
)
def test_gv4_only_true_turns_the_override_on(value: str | None, *, on: bool) -> None:
    assert override_on(value) is on
    env = {} if value is None else {OVERRIDE_ENV: value}
    assert engines_without_gvisor(env) is on
    assert release_default_wire(env=env)["isolationMin"] == ("docker" if on else "gvisor")
    assert release_floor(override=on) is (RuntimeMode.DOCKER_HARDENED if on else RuntimeMode.GVISOR)


def test_gv5_an_org_floor_above_gvisor_keeps_the_plain_reason() -> None:
    refused = isolation_refusal(RuntimeMode.MICROVM)
    assert refused.reason == "mode_unavailable"
    assert isolation_refusal(RuntimeMode.K8S).reason == GVISOR_UNAVAILABLE

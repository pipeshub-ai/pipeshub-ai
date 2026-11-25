"""ADR-CAP-02: one SANDBOX_MAX_TOTAL, read and sized the same way by the app and the sandbox broker, from the daemon
that runs the sandboxes; in broker mode the app takes the machine and the ceiling from the broker."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.sandbox import capacity as capacity_module
from app.agent_loop_lib.sandbox.capacity import (
    BROKER_CEILING,
    BROKER_MEASURED,
    CapacitySettings,
    DockerUnreachable,
    SandboxCapacity,
    broker_detail,
    build_daemon_capacity,
    build_sandbox_capacity,
    machine_from_broker,
    read_broker_machine_size,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

GiB = 2**30
# Neither machine is this test host, so no process limit is read from it and the terms are fixed.
SMALL = {"MemTotal": 16 * GiB, "NCPU": 8, "KernelVersion": "0.0.0-not-this-host"}
LARGE = {"MemTotal": 64 * GiB, "NCPU": 16, "KernelVersion": "0.0.0-not-this-host"}


@pytest.fixture(autouse=True)
def _own_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings warn once per process; these tests must not use up a warning another test expects."""
    monkeypatch.setattr(capacity_module, "_warned", set())


def _info(info: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def read() -> dict[str, Any]:
        return info

    return read


async def _sized(capacity: SandboxCapacity) -> SandboxCapacity:
    capacity.start_sizing()
    await capacity.wait_measured(5)
    assert capacity.measured
    return capacity


async def _app(env: dict[str, str], info: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> SandboxCapacity:
    monkeypatch.setattr(capacity_module, "_docker_info", _info(info))
    return await _sized(build_sandbox_capacity("docker", env))


async def _broker(env: dict[str, str], info: dict[str, Any]) -> SandboxCapacity:
    return await _sized(build_daemon_capacity(_info(info), env))


# SANDBOX_MAX_TOTAL -> the ceiling on each machine. Unset, blank or unreadable is sized from the machine:
# 16 GiB / 8 CPUs is min(16 x 0.25 / 0.5, 8 x 0.75 / 0.5) = min(8, 12); 64 GiB / 16 CPUs is min(32, 24).
# Zero or less is the largest ceiling there is, never "no ceiling".
CEILINGS = [
    (None, 8, 24),
    ("", 8, 24),
    (" 7 ", 7, 7),
    ("0", 1024, 1024),
    ("-3", 1024, 1024),
    ("lots", 8, 24),
    ("1.5", 8, 24),
]


@pytest.mark.parametrize(("raw", "small", "large"), CEILINGS)
async def test_cap02_the_broker_and_the_app_read_one_variable_one_way(
    raw: str | None, small: int, large: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {} if raw is None else {"SANDBOX_MAX_TOTAL": raw}
    for info, want in ((SMALL, small), (LARGE, large)):
        broker = await _broker(env, info)
        app = await _app(env, info, monkeypatch)
        assert broker.ceiling() == app.ceiling() == want
        assert broker.plan.terms == app.plan.terms


async def test_cap02_the_broker_sizes_from_the_sandbox_host_a_deployment_passes_in() -> None:
    env = {
        "SANDBOX_HOST_MEMORY_BYTES": str(8 * GiB),
        "SANDBOX_HOST_CPU_MILLIS": "4000",
        "SANDBOX_MEMORY_SHARE": "0.9",
    }
    broker = await _broker(env, LARGE)
    assert broker.plan.machine.source == "helm_env"
    # min(8 GiB x 0.9 / 0.5 GiB, 4 x 0.75 / 0.5) = min(14, 6)
    assert broker.ceiling() == 6


def _health(detail: dict[str, str]) -> Callable[[], Awaitable[dict[str, str]]]:
    async def read() -> dict[str, str]:
        return detail

    return read


async def _app_in_broker_mode(env: dict[str, str], broker: SandboxCapacity) -> SandboxCapacity:
    settings = CapacitySettings.from_env(env)
    app = SandboxCapacity(
        "compose", settings,
        machine_reader=lambda: read_broker_machine_size("compose", settings, health_detail=_health(broker_detail(broker))),
    )
    return await _sized(app)


@pytest.mark.parametrize(
    ("broker_env", "app_env", "ceiling", "binding"),
    [
        # The same settings on both sides: the app's ceiling is the broker's.
        ({}, {}, 8, "memory"),
        # App copies sharing the broker's daemon each take their share of it.
        ({}, {"SANDBOX_CAPACITY_DIVISOR": "2"}, 4, "memory"),
        # The broker's own explicit ceiling bounds the app's share of it.
        ({"SANDBOX_MAX_TOTAL": "6"}, {}, 6, "broker"),
        ({"SANDBOX_MAX_TOTAL": "6"}, {"SANDBOX_CAPACITY_DIVISOR": "2"}, 3, "broker"),
        # An explicit app ceiling stays what the operator set.
        ({"SANDBOX_MAX_TOTAL": "6"}, {"SANDBOX_MAX_TOTAL": "5"}, 5, "explicit"),
    ],
)
async def test_cap02_in_broker_mode_the_app_sizes_from_the_brokers_daemon(
    broker_env: dict[str, str], app_env: dict[str, str], ceiling: int, binding: str,
) -> None:
    broker = await _broker(broker_env, SMALL)
    app = await _app_in_broker_mode(app_env, broker)
    assert app.plan.machine.source == "broker"
    assert (app.ceiling(), app.plan.binding) == (ceiling, binding)


async def test_cap02_a_broker_that_does_not_report_its_host_leaves_the_app_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capacity_module, "_docker_info", _info(SMALL))
    machine, _ = await read_broker_machine_size(
        "compose", CapacitySettings(), health_detail=_health({"engineVersion": "28.0.4"}),
    )
    assert machine.source == "docker_info"
    assert machine.ceiling is None


async def test_cap02_a_broker_not_yet_sized_or_not_answering_is_waited_for() -> None:
    with pytest.raises(DockerUnreachable):
        _ = machine_from_broker({BROKER_CEILING: "2", BROKER_MEASURED: "false"})

    async def down() -> dict[str, str]:
        raise ConnectionError("refused")

    with pytest.raises(DockerUnreachable):
        _ = await read_broker_machine_size("compose", CapacitySettings(), health_detail=down)


async def test_cap02_broker_mode_reads_the_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = await _broker({}, LARGE)

    async def health() -> dict[str, str]:
        return broker_detail(broker)

    monkeypatch.setattr(capacity_module, "_broker_health_detail", health)
    app = await _sized(build_sandbox_capacity("broker", {}))
    assert app.plan.machine.source == "broker"
    assert app.ceiling() == broker.ceiling() == 24


def test_cap02_an_unread_broker_reports_only_its_minimum() -> None:
    broker = build_daemon_capacity(_info(SMALL), {})
    assert broker_detail(broker) == {BROKER_CEILING: "2", BROKER_MEASURED: "false"}

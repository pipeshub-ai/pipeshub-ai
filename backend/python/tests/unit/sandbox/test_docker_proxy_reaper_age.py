"""The reaper ages a container from its latest start or exit, so a slow
input upload before the start does not use up the run's time."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.sandbox.docker_proxy import _docker_time

from .test_docker_proxy_reaper import NOW, _container, _reaper, daemon  # noqa: F401  (fixture)

if TYPE_CHECKING:
    from .test_docker_proxy_reaper import _Daemon


def _iso(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def test_daemon_times_are_parsed() -> None:
    assert _docker_time("0001-01-01T00:00:00Z") is None
    assert _docker_time("") is None
    assert _docker_time("garbage") is None
    assert abs(_docker_time(_iso(NOW)) - (NOW + 0.123456)) < 1e-3  # type: ignore[operator]
    assert _docker_time("2026-01-02T03:04:05Z") is not None


async def test_a_run_that_started_late_is_not_reaped(daemon: tuple[_Daemon, str]) -> None:  # noqa: F811
    fake, sock = daemon
    # Created 500 s ago, but the upload took 300 s: it has run for 200 s.
    fake.containers = [_container("late", 500)]
    fake.inspect["late"] = {"State": {"StartedAt": _iso(NOW - 200), "FinishedAt": "0001-01-01T00:00:00Z"}}
    assert await _reaper(sock).reap_once() == 0
    assert [c["Id"] for c in fake.containers] == ["late"]


async def test_a_run_that_started_long_ago_is_reaped(daemon: tuple[_Daemon, str]) -> None:  # noqa: F811
    fake, sock = daemon
    fake.containers = [_container("stuck", 900)]
    fake.inspect["stuck"] = {"State": {"StartedAt": _iso(NOW - 800), "FinishedAt": "0001-01-01T00:00:00Z"}}
    assert await _reaper(sock).reap_once() == 1


async def test_a_container_still_receiving_its_inputs_waits_for_the_body_timeout(
    daemon: tuple[_Daemon, str],  # noqa: F811
) -> None:
    fake, sock = daemon
    fake.containers = [_container("uploading", 900, state="created")]
    assert await _reaper(sock).reap_once() == 0
    fake.containers = [_container("abandoned", 900 + 1800, state="created")]
    assert await _reaper(sock).reap_once() == 1


async def test_an_unreadable_start_does_not_reap_a_container_that_may_still_run(
    daemon: tuple[_Daemon, str],  # noqa: F811
) -> None:
    fake, sock = daemon
    # Created 500 s ago; its start cannot be read, and the upload may have taken most of that.
    fake.containers = [_container("unknown", 500)]
    fake.inspect_fails.add("unknown")
    assert await _reaper(sock).reap_once() == 0
    # Even far past the upload bound: a client may upload several archives
    # before starting it, so it is left for a pass that can read its start.
    fake.containers = [_container("unknown", 420 + 1800 + 10)]
    assert await _reaper(sock).reap_once() == 0
    fake.inspect_fails.discard("unknown")
    fake.inspect["unknown"] = {"State": {"StartedAt": _iso(NOW - 800), "FinishedAt": "0001-01-01T00:00:00Z"}}
    assert await _reaper(sock).reap_once() == 1


async def test_a_state_without_readable_times_does_not_reap_a_container_that_may_still_run(
    daemon: tuple[_Daemon, str],  # noqa: F811
) -> None:
    fake, sock = daemon
    fake.containers = [_container("blank", 500)]
    fake.inspect["blank"] = {"State": {}}
    assert await _reaper(sock).reap_once() == 0
    fake.inspect["blank"] = {"State": {"StartedAt": "0001-01-01T00:00:00Z", "FinishedAt": "garbage"}}
    assert await _reaper(sock).reap_once() == 0
    fake.containers = [_container("blank", 420 + 1800 + 10)]
    assert await _reaper(sock).reap_once() == 0

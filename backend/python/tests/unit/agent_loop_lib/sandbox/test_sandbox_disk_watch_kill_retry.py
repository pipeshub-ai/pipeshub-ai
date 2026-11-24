"""A disk-watch kill that fails is retried (with a forced removal) until the
container is stopped, so an over-quota run cannot keep writing."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import app.agent_loop_lib.sandbox.coding.docker as docker_mod
from app.agent_loop_lib.sandbox.coding.docker import _DiskWatch

if TYPE_CHECKING:
    import pytest


class _Refused(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.response = MagicMock(status_code=status)


def test_a_failed_kill_is_retried_until_the_container_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "_DISK_WATCH_INTERVAL_S", 0.01)
    client = MagicMock()
    calls: list[str] = []

    def kill(_id: str) -> None:
        calls.append("kill")
        if len(calls) < 4:
            raise ConnectionError("daemon busy")

    def remove(_id: str, force: bool = False) -> None:
        calls.append("remove")
        raise ConnectionError("daemon busy")

    client.api.kill.side_effect = kill
    client.api.remove_container.side_effect = remove
    _DiskWatch(client, "c" * 64, 1)._kill()
    assert calls == ["kill", "remove", "kill", "remove", "kill"]


def test_a_container_that_already_stopped_ends_the_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "_DISK_WATCH_INTERVAL_S", 0.01)
    client = MagicMock()
    client.api.kill.side_effect = _Refused(409)
    _DiskWatch(client, "c" * 64, 1)._kill()
    assert client.api.kill.call_count == 1
    client.api.remove_container.assert_not_called()


def test_the_retries_end_when_the_run_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "_DISK_WATCH_INTERVAL_S", 0.01)
    client = MagicMock()
    client.api.kill.side_effect = ConnectionError("down")
    client.api.remove_container.side_effect = ConnectionError("down")
    watch = _DiskWatch(client, "c" * 64, 1)
    watch._stop.set()
    watch._kill()
    assert client.api.kill.call_count == 1

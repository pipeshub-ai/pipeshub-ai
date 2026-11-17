"""The per-run disk quota holds even where the daemon ignores it.

``storage_opt size`` is accepted and enforced by overlay2 (xfs + pquota),
btrfs and zfs, but accepted and ignored by the containerd snapshotters, so a
run could write until the daemon's disk is full. When the daemon does not
enforce it, the app polls each run's writable-layer size (``SizeRw``) and
stops the run past the quota, with a message the model can act on.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

import app.agent_loop_lib.sandbox.coding.docker as docker_mod
import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

from .test_docker_coding_sandbox import (  # noqa: F401  (fixtures)
    _fake_docker_module,
    _fresh_docker_provider,
    _make_tar,
)

MiB = 1024 * 1024


@pytest.fixture(autouse=True)
def _fresh_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mod, "_disk_watch_limit", None, raising=False)
    monkeypatch.setattr(docker_mod, "_DISK_WATCH_INTERVAL_S", 0.02, raising=False)
    monkeypatch.setattr(ef, "_storage_opt_usable", None)
    monkeypatch.setattr(ef, "_oom_score_adj_usable", True)
    monkeypatch.setenv("SANDBOX_DISK_QUOTA", "1m")
    monkeypatch.delenv("SANDBOX_DISK_WATCHDOG", raising=False)


def _client(driver_info: dict, sizes: list[int]) -> tuple[MagicMock, MagicMock, threading.Event]:
    """A client whose run container grows through `sizes` (SizeRw per
    inspect) and only exits once it is killed."""
    killed = threading.Event()
    container = MagicMock()
    container.id = "c0ffee" * 10

    def _wait(timeout: float | None = None) -> dict:
        if not killed.wait(5):
            return {"StatusCode": 0}
        return {"StatusCode": 137}

    container.wait.side_effect = _wait
    container.logs.side_effect = lambda **kw: b""
    container.get_archive.side_effect = lambda path: (iter([_make_tar({})]), {})

    client = MagicMock()
    client.containers.create.return_value = container
    client.info.return_value = driver_info
    remaining = list(sizes)

    def _result(_res: object, _json: object) -> dict:
        return {"SizeRw": remaining.pop(0) if len(remaining) > 1 else remaining[0]}

    client.api._result.side_effect = _result
    client.api.kill.side_effect = lambda _id: killed.set()
    return client, container, killed


class TestTheWatchdogStopsARunPastTheQuota:
    async def test_containerd_snapshotter_is_watched_and_the_run_is_stopped(self, tmp_path) -> None:
        client, _container, killed = _client(
            {"Driver": "overlayfs", "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]},
            [10, 512 * 1024, 3 * MiB],
        )
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", timeout=30))

        assert killed.is_set()
        assert result.success is False
        assert "wrote more than 1m" in result.stderr
        assert result.error_analysis is not None
        assert "disk" in result.error_analysis.message
        assert result.error_analysis.is_retryable is False

    async def test_an_install_past_the_quota_is_stopped(self, tmp_path) -> None:
        client, _container, killed = _client({"Driver": "overlayfs"}, [5 * MiB])
        client.networks.get.return_value.attrs = {"IPAM": {"Config": [{"Subnet": "10.9.0.0/16"}]}}
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        sandbox._firewalled = lambda command, cidrs, token: {"command": command}  # type: ignore[method-assign]

        code, _out, err = sandbox._install_in_container(
            client, "egress", "/deps", str(tmp_path / "deps"), ["mpmath"], {"command": ["true"]},
        )

        assert killed.is_set()
        assert code != 0
        assert "Package installation was stopped" in err

    async def test_a_run_under_the_quota_is_left_alone(self, tmp_path) -> None:
        client, container, killed = _client({"Driver": "overlayfs"}, [100])
        container.wait.side_effect = lambda timeout=None: {"StatusCode": 0}
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert not killed.is_set()
        assert result.success is True
        assert "disk" not in result.stderr


class TestWhenToWatch:
    def test_overlay2_that_accepted_the_quota_enforces_it(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(ef, "_storage_opt_usable", True)
        client = MagicMock()
        client.info.return_value = {"Driver": "overlay2", "DriverStatus": [["Backing Filesystem", "xfs"]]}
        assert docker_mod._disk_watch_limit_for(client) == 0

    def test_a_containerd_snapshotter_does_not_and_says_so_once(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(ef, "_storage_opt_usable", True)
        client = MagicMock()
        client.info.return_value = {"Driver": "overlayfs"}
        with caplog.at_level("WARNING"):
            assert docker_mod._disk_watch_limit_for(client) == MiB
            assert docker_mod._disk_watch_limit_for(client) == MiB
        warnings = [r for r in caplog.records if "does not enforce" in r.getMessage()]
        assert len(warnings) == 1

    @pytest.mark.parametrize("info", [
        {"Driver": "btrfs"},
        {"Driver": "zfs", "DriverStatus": []},
        {"Driver": "overlay2", "DriverStatus": [["Backing Filesystem", "extfs"]]},
    ])
    def test_unknown_status_or_a_non_xfs_overlay2_is_watched(self, monkeypatch, info) -> None:
        monkeypatch.setattr(ef, "_storage_opt_usable", True)
        client = MagicMock()
        client.info.return_value = info
        assert docker_mod._disk_watch_limit_for(client) == MiB

    def test_a_rejected_quota_is_watched(self, monkeypatch) -> None:
        monkeypatch.setattr(ef, "_storage_opt_usable", False)
        client = MagicMock()
        client.info.return_value = {"Driver": "overlay2"}
        assert docker_mod._disk_watch_limit_for(client) == MiB

    def test_off_and_no_quota_disable_it(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_DISK_WATCHDOG", "off")
        assert docker_mod._disk_watch_limit_for(MagicMock()) == 0
        monkeypatch.setattr(docker_mod, "_disk_watch_limit", None)
        monkeypatch.setenv("SANDBOX_DISK_WATCHDOG", "auto")
        monkeypatch.setenv("SANDBOX_DISK_QUOTA", "off")
        assert docker_mod._disk_watch_limit_for(MagicMock()) == 0

    def test_on_watches_even_an_enforcing_daemon(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_DISK_WATCHDOG", "on")
        monkeypatch.setattr(ef, "_storage_opt_usable", True)
        client = MagicMock()
        client.info.return_value = {"Driver": "overlay2"}
        assert docker_mod._disk_watch_limit_for(client) == MiB

    @pytest.mark.parametrize(("text", "size"), [
        ("10g", 10 * 1024**3), ("512m", 512 * MiB), ("1.5GB", int(1.5 * 1024**3)), ("2048", 2048),
        ("bogus", None),
    ])
    def test_docker_size_strings(self, text: str, size: int | None) -> None:
        assert docker_mod._parse_size(text) == size

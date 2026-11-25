"""SEC-ISO-06 (C2.18): the Docker broker's bounds on what a sandbox can write, decided without a daemon.

A tmpfs gets an inode count of its own size, and a persistent workspace (a subpath of the shared data volume on the
host's disk) a quota the broker measures, as the kubelet does ephemeral storage: over it, the sandbox is killed and
refused from then on, and its folder (a cache of the workspace revision store) erased.
"""

from __future__ import annotations

import asyncio
from typing import Any

import docker.errors
import pytest
import requests

from app.harness.sandbox.docker_broker import (
    TMPFS_INODES_PER_MB,
    WORKSPACE_INODES_PER_MB,
    WORKSPACE_QUOTA_EXCEEDED,
    DockerCapabilities,
    _Sandbox,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.sandbox.errors import SandboxRefusal
from harness_core.sandbox.types import MountSpec, Resources
from tests.support.settle import settle
from tests.unit.harness.sandbox_broker.test_docker_broker import (
    LEASE,
    _broker_on,  # pyright: ignore[reportPrivateUsage]
    _FakeApi,  # pyright: ignore[reportPrivateUsage]
    _FakeProvider,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)

QUOTA_MB = 64
SUBPATH = DockerCapabilities(reachable=True, engine_version="28.0.4", runtimes=frozenset({"runc"}), exec_transport="unix")
SOURCE = "org-a/ses-1/ws"


def _status_error(status: int) -> docker.errors.APIError:
    response = requests.Response()
    response.status_code = status
    return docker.errors.APIError(str(status), response=response)


class _QuotaApi(_FakeApi):
    """Answers the guard's measure with ``usage`` per container (``(code, output)``), and runs helpers."""

    def __init__(self) -> None:
        super().__init__()
        self.usage: dict[str, tuple[int, bytes]] = {}
        self.gone: set[str] = set()
        self.killed: list[str] = []
        self.helpers: list[list[str]] = []
        self.helper_exit = 0
        self._execs: dict[str, str] = {}

    def exec_create(self, container: str, cmd: list[str], **_: Any) -> dict[str, str]:  # noqa: ANN401
        if container in self.gone:
            raise _status_error(409)
        exec_id = f"exec-{len(self._execs)}"
        self._execs[exec_id] = container
        return {"Id": exec_id}

    def exec_start(self, exec_id: str, **_: Any) -> bytes:  # noqa: ANN401
        return self.usage[self._execs[exec_id]][1]

    def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        return {"Running": False, "ExitCode": self.usage[self._execs[exec_id]][0]}

    def kill(self, container: str) -> None:
        self.killed.append(container)

    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        if kwargs.get("command") and kwargs["command"][:2] == ["sh", "-c"]:
            self.helpers.append(kwargs["command"])
            return {"Id": "helper"}
        return super().create_container(image, **kwargs)

    def start(self, _: str) -> None:
        return None

    def wait(self, _: str, **__: Any) -> dict[str, int]:  # noqa: ANN401
        return {"StatusCode": self.helper_exit}

    def remove_container(self, *_: Any, **__: Any) -> None:  # noqa: ANN401
        return None


def _provider(api: _QuotaApi) -> _FakeProvider:
    provider = _FakeProvider()
    provider.client.api = api
    return provider


def _held(broker: Any, sandbox_id: str, *, source: str = SOURCE, read_only: bool = False) -> _Sandbox:  # noqa: ANN401
    sandbox = _Sandbox(sandbox_id, f"ctr-{sandbox_id}", _spec(mounts=(MountSpec("/ws", source=source, read_only=read_only),)))
    broker._sandboxes[sandbox_id] = sandbox  # pyright: ignore[reportPrivateUsage]
    _ = broker.ledger.record(f"k-{sandbox_id}", sandbox_id, LEASE)
    return sandbox


def _quota_broker(api: _QuotaApi, **kwargs: Any) -> Any:  # noqa: ANN401
    return _broker_on(
        _provider(api), SUBPATH, data_volume="data", helper_image="img", workspace_quota_mb=QUOTA_MB, **kwargs
    )


def _usage(kb: int, inodes: int = 1) -> tuple[int, bytes]:
    return 0, f"{kb} {inodes}\n".encode()


async def test_sec_iso_06_every_tmpfs_has_an_inode_count_of_its_own_size() -> None:
    provider = _FakeProvider()
    with pytest.raises(Exception):  # noqa: B017 - the fake stops after recording the create
        await _broker_on(provider).acquire(_spec(resources=Resources(scratch_mb=64)), lease=LEASE, idempotency_key="k")
    [host_config] = provider.client.api.host_configs
    assert f"nr_inodes={64 * TMPFS_INODES_PER_MB}" in host_config["Tmpfs"]["/tmp"]
    assert host_config["Ulimits"] == [{"Name": "core", "Soft": 0, "Hard": 0}]
    # Only a proxied sandbox's resolver is pointed nowhere; this one has no network at all.
    assert "Dns" not in host_config


async def test_sec_iso_06_a_scratch_volume_is_inode_bounded_too() -> None:
    created: list[dict[str, Any]] = []

    class _Volumes(_FakeApi):
        def create_volume(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            created.append(kwargs)
            return {}

    provider = _FakeProvider()
    provider.client.api = _Volumes()
    with pytest.raises(Exception):  # noqa: B017
        await _broker_on(provider).acquire(_spec(resources=Resources(scratch_mb=32)), lease=LEASE, idempotency_key="k")
    [volume] = created
    assert f"nr_inodes={32 * TMPFS_INODES_PER_MB}" in volume["driver_opts"]["o"]


async def test_sec_iso_06_with_a_quota_no_single_file_may_outgrow_it() -> None:
    api = _QuotaApi()
    with pytest.raises(Exception):  # noqa: B017
        await _quota_broker(api).acquire(_spec(), lease=LEASE, idempotency_key="k")
    [host_config] = api.host_configs
    limit = QUOTA_MB * 1024 * 1024
    assert host_config["Ulimits"] == [
        {"Name": "core", "Soft": 0, "Hard": 0},
        {"Name": "fsize", "Soft": limit, "Hard": limit},
    ]


async def test_sec_iso_06_a_workspace_inside_its_quota_is_left_alone() -> None:
    api = _QuotaApi()
    broker = _quota_broker(api)
    sandbox = _held(broker, "sbx_1")
    api.usage["ctr-sbx_1"] = _usage(QUOTA_MB * 1024, QUOTA_MB * WORKSPACE_INODES_PER_MB)
    assert await broker.check_workspaces() == 1
    assert (api.killed, api.helpers) == ([], [])
    assert broker.live("sbx_1") is sandbox


@pytest.mark.parametrize(
    "answer",
    [_usage(QUOTA_MB * 1024 + 1), _usage(1, QUOTA_MB * WORKSPACE_INODES_PER_MB + 1), (1, b"unreadable\n")],
    ids=["bytes", "inodes", "unreadable"],
)
async def test_sec_iso_06_a_workspace_over_its_quota_is_ended_refused_and_erased(answer: tuple[int, bytes]) -> None:
    api = _QuotaApi()
    broker = _quota_broker(api)
    _ = _held(broker, "sbx_1")
    sibling = _held(broker, "sbx_2", source="org-a/ses-2/ws")
    api.usage |= {"ctr-sbx_1": answer, "ctr-sbx_2": _usage(1)}
    _ = await broker.check_workspaces()
    assert api.killed == ["ctr-sbx_1"]
    [erase] = api.helpers
    assert erase[-1] == f"/data/{SOURCE}"
    with pytest.raises(SandboxRefusal) as refused:
        broker.live("sbx_1")
    assert refused.value.reason == WORKSPACE_QUOTA_EXCEEDED
    assert f"{QUOTA_MB} MB" in str(refused.value)
    assert broker.live("sbx_2") is sibling
    # Measured no more: it is over.
    assert await broker.check_workspaces() == 1


@pytest.mark.parametrize("answer", [(126, b""), (0, b"garbage"), (1, b"")], ids=["did-not-run", "unparsable", "silent"])
async def test_sec_iso_06_a_measure_that_cannot_run_is_retried_and_fails_closed_only_when_it_keeps_failing(
    answer: tuple[int, bytes],
) -> None:
    """A sandbox briefly at its pids limit cannot start ``du``: one such round is no verdict, three in a row are."""
    api = _QuotaApi()
    broker = _quota_broker(api)
    _ = _held(broker, "sbx_1")
    api.usage["ctr-sbx_1"] = answer
    for _ in range(2):
        _ = await broker.check_workspaces()
    assert api.killed == []
    api.usage["ctr-sbx_1"] = _usage(1)
    _ = await broker.check_workspaces()
    api.usage["ctr-sbx_1"] = answer
    for _ in range(2):
        _ = await broker.check_workspaces()
    assert api.killed == [], "a good measure in between starts the count again"
    _ = await broker.check_workspaces()
    assert api.killed == ["ctr-sbx_1"]
    with pytest.raises(SandboxRefusal):
        broker.live("sbx_1")


async def test_sec_iso_06_every_sandbox_writing_the_same_folder_is_ended() -> None:
    api = _QuotaApi()
    broker = _quota_broker(api)
    _ = _held(broker, "sbx_1")
    _ = _held(broker, "sbx_2")
    reader = _held(broker, "sbx_3", read_only=True)
    api.usage |= {"ctr-sbx_1": _usage(QUOTA_MB * 2048), "ctr-sbx_2": _usage(QUOTA_MB * 2048)}
    _ = await broker.check_workspaces()
    assert sorted(api.killed) == ["ctr-sbx_1", "ctr-sbx_2"]
    assert len(api.helpers) == 1
    assert broker.live("sbx_3") is reader


async def test_sec_iso_06_a_stopped_sandbox_is_skipped_not_judged() -> None:
    api = _QuotaApi()
    broker = _quota_broker(api)
    sandbox = _held(broker, "sbx_1")
    api.gone.add("ctr-sbx_1")
    assert await broker.check_workspaces() == 1
    assert (api.killed, api.helpers, sandbox.over_quota) == ([], [], False)


async def test_sec_iso_06_a_folder_that_could_not_be_erased_refuses_its_next_acquire_until_it_is() -> None:
    api = _QuotaApi()
    api.helper_exit = 1
    broker = _quota_broker(api)
    _ = _held(broker, "sbx_1")
    api.usage["ctr-sbx_1"] = _usage(QUOTA_MB * 4096)
    _ = await broker.check_workspaces()
    spec = _spec(mounts=(MountSpec("/ws", source=SOURCE),))
    with pytest.raises(SandboxRefusal) as refused:
        await broker.acquire(spec, lease=LEASE, idempotency_key="again")
    assert refused.value.reason == WORKSPACE_QUOTA_EXCEEDED
    api.helper_exit = 0
    with pytest.raises(Exception) as created:  # the fake stops at the create: the refusal is gone
        await broker.acquire(spec, lease=LEASE, idempotency_key="again")
    assert not isinstance(created.value, SandboxRefusal)


async def test_sec_iso_06_copied_mounts_and_brokers_without_a_quota_are_not_measured() -> None:
    api = _QuotaApi()
    copied = _quota_broker(api, copy_persistent_mounts=True)
    _ = _held(copied, "sbx_1")
    assert await copied.check_workspaces() == 0
    plain = _broker_on(_provider(api), SUBPATH, data_volume="data")
    _ = _held(plain, "sbx_2")
    assert await plain.check_workspaces() == 0


async def test_sec_iso_06_the_guard_ends_with_the_last_workspace_and_on_aclose() -> None:
    api = _QuotaApi()
    broker = _quota_broker(api, workspace_check_s=0.01)
    _ = _held(broker, "sbx_1")
    api.usage["ctr-sbx_1"] = _usage(1)
    broker._watch()  # pyright: ignore[reportPrivateUsage]
    guard = broker._guard  # pyright: ignore[reportPrivateUsage]
    assert guard is not None
    await asyncio.sleep(0.05)
    assert not guard.done()
    broker._sandboxes.clear()  # pyright: ignore[reportPrivateUsage]
    await settle(guard)
    assert broker._guard is None  # pyright: ignore[reportPrivateUsage]
    _ = _held(broker, "sbx_2")
    api.usage["ctr-sbx_2"] = _usage(1)
    broker._watch()  # pyright: ignore[reportPrivateUsage]
    await broker.aclose()
    assert broker._guard is None  # pyright: ignore[reportPrivateUsage]


def test_sec_iso_06_a_quota_is_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        _ = _broker_on(_FakeProvider(), workspace_quota_mb=0)

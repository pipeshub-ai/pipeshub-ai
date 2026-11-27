"""CLEANUP-G6: a starting broker removes what its own install left that nothing can use any more, and only that.

A broker restart or a host reboot left stopped sandboxes, helper containers, scratch volumes and binding markers
behind: the reaps match running work by run labels, and the orphan sweep only kept sign-ins. Everything the broker
creates now carries its install's label, and ``reconcile`` (run once at start) removes this install's dead
containers, their volumes and orphan markers. A running sandbox stays: a restarted broker adopts it by key, and the
run sweeper, its lifetime or the orphan farewell end it. Another install's resources, and anything without an
install label (made before this change), are never touched.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.sandbox.types import FAREWELL_LABEL
from harness_engines.claude_code.signin import FAREWELL
from tests.unit.harness.sandbox_broker.test_docker_broker import (
    LEASE,
    _broker_on,  # pyright: ignore[reportPrivateUsage]
    _FakeApi,  # pyright: ignore[reportPrivateUsage]
    _FakeProvider,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)

NS = "io.pipeshub.sandbox-broker"
OURS = "install-a"
THEIRS = "install-b"
KEPT = {"orgId": "org-1", "sessionId": "ses-1", "pipeshub.signin.kept": "1", FAREWELL_LABEL: "claude_code"}


class _Conflict(Exception):
    """What the daemon answers an exec in a container that is not running."""

    def __init__(self) -> None:
        super().__init__("container is not running")
        self.response = type("Response", (), {"status_code": 409})()


def _matches(labels: dict[str, str], wanted: list[str]) -> bool:
    return all(labels.get(f.split("=", 1)[0]) == f.split("=", 1)[1] if "=" in f else f in labels for f in wanted)


class _HostApi(_FakeApi):
    """A daemon's containers and volumes by labels, and what was removed or executed."""

    def __init__(self) -> None:
        super().__init__()
        self.containers_by_id: dict[str, dict[str, Any]] = {}
        self.volumes_by_name: dict[str, dict[str, str]] = {}
        self.removed: list[str] = []
        self.removed_volumes: list[str] = []
        self.execs: list[str] = []

    def containers(self, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        wanted = (kwargs.get("filters") or {}).get("label", [])
        return [
            {"Id": cid, **info}
            for cid, info in self.containers_by_id.items()
            if cid not in self.removed and _matches(info["Labels"], wanted)
            and (kwargs.get("all") or info["State"] == "running")
        ]

    def volumes(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        wanted = (kwargs.get("filters") or {}).get("label", [])
        return {
            "Volumes": [
                {"Name": name, "Labels": labels}
                for name, labels in self.volumes_by_name.items()
                if name not in self.removed_volumes and _matches(labels, wanted)
            ]
        }

    def exec_create(self, container: str, cmd: list[str], **_: Any) -> dict[str, str]:  # noqa: ANN401
        self.execs.append(container)
        if self.containers_by_id[container]["State"] != "running":
            raise _Conflict
        return {"Id": f"exec-{container}"}

    def exec_start(self, exec_id: str, **_: Any) -> bytes:  # noqa: ANN401
        return b""

    def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        return {"Running": False, "ExitCode": 0}

    def remove_container(self, container: str, **_: Any) -> None:  # noqa: ANN401
        self.removed.append(container)

    def remove_volume(self, name: str, **_: Any) -> None:  # noqa: ANN401
        self.removed_volumes.append(name)


def _labels(install: str | None, sandbox_id: str, role: str, created: int = 0, **extra: str) -> dict[str, str]:
    labels = {f"{NS}.managed": "1", f"{NS}.id": sandbox_id, f"{NS}.role": role, f"{NS}.created": str(created),
              f"{NS}.key": f"key-{sandbox_id}", **extra}
    if install is not None:
        labels[f"{NS}.install"] = install
    return labels


def _container(api: _HostApi, cid: str, state: str, labels: dict[str, str]) -> str:
    api.containers_by_id[cid] = {"State": state, "Labels": labels, "Names": [f"/{cid}"]}
    return cid


def _volume(api: _HostApi, name: str, labels: dict[str, str]) -> str:
    api.volumes_by_name[name] = labels
    return name


def _broker(api: _HostApi, install: str | None = OURS) -> DockerSandboxBroker:
    provider = _FakeProvider()
    provider.client.api = api
    return _broker_on(provider, install_id=install, farewells={"claude_code": FAREWELL})


async def test_cleanup_g6_1_a_starting_broker_removes_its_install_s_dead_sandboxes_helpers_and_orphan_volumes() -> None:
    api = _HostApi()
    broker = _broker(api)
    broker._clock.advance(3600)  # pyright: ignore[reportPrivateUsage]
    stopped = _container(api, "ctr-stopped", "exited", _labels(OURS, "sbx_1", "sandbox"))
    stopped_scratch = _volume(api, "pipeshub-sbx-sbx_1-0", _labels(OURS, "sbx_1", "volume"))
    created_only = _container(api, "ctr-created", "created", _labels(OURS, "sbx_2", "sandbox"))
    dead_warm = _container(api, "ctr-warm-dead", "exited", _labels(OURS, "sbx_3", "warm"))
    helper = _container(api, "ctr-helper", "created", _labels(OURS, "", "helper"))
    orphan_scratch = _volume(api, "pipeshub-sbx-sbx_9-0", _labels(OURS, "sbx_9", "volume"))
    orphan_marker = _volume(api, "pipeshub-sbx-sbx_8-bind", _labels(OURS, "sbx_8", "binding"))

    running = _container(api, "ctr-running", "running", _labels(OURS, "sbx_4", "sandbox"))
    running_scratch = _volume(api, "pipeshub-sbx-sbx_4-0", _labels(OURS, "sbx_4", "volume"))
    warm = _container(api, "ctr-warm", "running", _labels(OURS, "sbx_5", "warm"))
    theirs = _container(api, "ctr-theirs", "exited", _labels(THEIRS, "sbx_6", "sandbox"))
    theirs_scratch = _volume(api, "pipeshub-sbx-sbx_7-0", _labels(THEIRS, "sbx_7", "volume"))
    theirs_helper = _container(api, "ctr-theirs-helper", "created", _labels(THEIRS, "", "helper"))
    unlabelled = _container(api, "ctr-old", "exited", _labels(None, "sbx_10", "sandbox"))
    unlabelled_scratch = _volume(api, "pipeshub-sbx-sbx_11-0", _labels(None, "sbx_11", "volume"))

    assert await broker.reconcile() == 4

    assert sorted(api.removed) == sorted([stopped, created_only, dead_warm, helper])
    assert sorted(api.removed_volumes) == sorted([stopped_scratch, orphan_scratch, orphan_marker])
    for kept in (running, warm, theirs, theirs_helper, unlabelled):
        assert kept not in api.removed
    for kept in (running_scratch, theirs_scratch, unlabelled_scratch):
        assert kept not in api.removed_volumes


async def test_cleanup_g6_2_a_helper_still_in_use_is_left_for_its_own_finally() -> None:
    api = _HostApi()
    broker = _broker(api)
    now = int(broker._clock.now().timestamp())  # pyright: ignore[reportPrivateUsage]
    fresh = _container(api, "ctr-helper", "created", _labels(OURS, "", "helper", created=now - 5))
    assert await broker.reconcile() == 0
    assert fresh not in api.removed


async def test_cleanup_g6_3_a_stopped_kept_sign_in_is_logged_out_if_it_can_be_and_the_person_told_otherwise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    api = _HostApi()
    broker = _broker(api)
    kept = _container(api, "ctr-kept", "exited", _labels(OURS, "sbx_1", "sandbox", **KEPT))
    state = _volume(api, "pipeshub-sbx-sbx_1-0", _labels(OURS, "sbx_1", "volume", **KEPT))

    assert await broker.reconcile() == 1

    # The farewell was tried; a stopped container cannot run it, and its tmpfs login state went with the stop.
    assert api.execs == [kept]
    assert api.removed == [kept] and api.removed_volumes == [state]
    messages = [r.getMessage() for r in caplog.records]
    [signed_out] = [m for m in messages if m.startswith("engine_account.signed_out")]
    assert "reason=reconciled" in signed_out and "outcome=gone" in signed_out
    [needed] = [m for m in messages if m.startswith("engine_account.vendor_sign_out_needed")]
    assert "sandboxId=sbx_1" in needed and "orgId=org-1" in needed and "sessionId=ses-1" in needed
    assert "farewell=claude_code" in needed


async def test_cleanup_g6_4_a_farewell_that_ran_needs_no_vendor_sign_out(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    api = _HostApi()
    broker = _broker(api)
    _ = _container(api, "ctr-kept", "running", _labels(OURS, "sbx_1", "sandbox", **KEPT))
    assert await broker.reap(labels={"sessionId": "ses-1"}, older_than_s=0) == 1
    assert not [r for r in caplog.records if r.getMessage().startswith("engine_account.vendor_sign_out_needed")]


async def test_cleanup_g6_5_a_broker_without_an_install_id_reconciles_nothing() -> None:
    api = _HostApi()
    broker = _broker(api, install=None)
    _ = _container(api, "ctr-stopped", "exited", _labels(None, "sbx_1", "sandbox"))
    _ = _volume(api, "pipeshub-sbx-sbx_9-0", _labels(None, "sbx_9", "volume"))
    assert await broker.reconcile() == 0
    assert api.removed == [] and api.removed_volumes == []


class _RecordingApi(_HostApi):
    """Records what the broker labels a sandbox, its scratch volumes and its helpers with."""

    def __init__(self) -> None:
        super().__init__()
        self.created_labels: list[dict[str, str]] = []
        self.volume_labels: list[dict[str, str]] = []

    def create_volume(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.volume_labels.append(dict(kwargs.get("labels") or {}))
        return {}

    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.created_labels.append(dict(kwargs.get("labels") or {}))
        raise RuntimeError("stop after recording")


async def test_cleanup_g6_6_everything_the_broker_creates_names_its_install() -> None:
    api = _RecordingApi()
    broker = _broker(api)
    with pytest.raises(Exception):  # noqa: B017 - the fake stops every create
        _ = await broker.acquire(_spec(), lease=LEASE, idempotency_key="k")
    [container] = api.created_labels
    [scratch] = api.volume_labels
    assert container[f"{NS}.install"] == OURS and scratch[f"{NS}.install"] == OURS
    assert broker._helper_labels()[f"{NS}.install"] == OURS  # pyright: ignore[reportPrivateUsage]


@pytest.mark.docker
async def test_cleanup_g6_7_on_a_real_daemon_a_restarted_broker_removes_only_its_install_s_stopped_sandbox(
    provider: Any,  # noqa: ANN401 - conftest fixtures
    namespace: str,
    volume_prefix: str,
    cleanup_namespace: None,
) -> None:
    from harness_core.ids import RandomIds
    from harness_core.sandbox.types import (
        MountSpec,
        Resources,
        RuntimeMode,
        SandboxSpec,
    )
    from harness_core.testing.clock import ManualClock
    from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

    spec = SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        mounts=(MountSpec("/work"),),
    )

    async def broker_of(install: str) -> DockerSandboxBroker:
        return await DockerSandboxBroker.create(
            provider=provider, clock=ManualClock(), ids=RandomIds(), allowed_images=frozenset({DOCKER_TEST_IMAGE}),
            label_namespace=namespace, volume_prefix=volume_prefix, install_id=install,
        )

    api = provider.client.api
    ours, theirs = await broker_of(OURS), await broker_of(THEIRS)
    stopped = await ours.acquire(spec, lease=LEASE, idempotency_key="stopped")
    running = await ours.acquire(spec, lease=LEASE, idempotency_key="running")
    other = await theirs.acquire(spec, lease=LEASE, idempotency_key="other")
    # What a host reboot leaves of a sandbox: its container stopped (no restart policy), its tmpfs gone.
    await provider.run_blocking(api.stop, ours.live(stopped.id).container_id, timeout=1)
    await provider.run_blocking(api.stop, theirs.live(other.id).container_id, timeout=1)

    def present(sandbox_id: str) -> tuple[int, int]:
        containers = api.containers(all=True, filters={"label": [f"{namespace}.id={sandbox_id}"]})
        volumes = (api.volumes(filters={"label": [f"{namespace}.id={sandbox_id}"]}) or {}).get("Volumes") or []
        return len(containers), len(volumes)

    assert await provider.run_blocking(present, stopped.id) == (1, 1)
    restarted = await broker_of(OURS)
    assert await restarted.reconcile() == 1
    assert await provider.run_blocking(present, stopped.id) == (0, 0)
    assert await provider.run_blocking(present, running.id) == (1, 1)
    assert await provider.run_blocking(present, other.id) == (1, 1)

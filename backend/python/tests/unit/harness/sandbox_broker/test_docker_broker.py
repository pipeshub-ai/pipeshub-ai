"""DB-1..DB-13: what the Docker broker adds beyond the contract suite."""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import docker.errors
import docker.types
import pytest

from app.harness.sandbox.docker_broker import (
    GVISOR_HOST_PIDS_BASE,
    GVISOR_HOST_PIDS_PER_TASK,
    SANDBOX_USER,
    DockerCapabilities,
    DockerSandboxBroker,
    probe,
)
from app.harness.sandbox.docker_policy import (
    HardeningPolicy,
    normalize_image,
    runsc_oci_seccomp,
    unsafe_runsc_args,
)
from harness_core.clock import SystemClock
from harness_core.failpoints import Arm, Kill9
from harness_core.ids import RandomIds
from harness_core.sandbox.errors import (
    BrokerUnavailable,
    ImageNotAllowed,
    InvalidSandboxRequest,
    InvalidSandboxSpec,
    IsolationUnavailable,
    PolicyUnenforceable,
    SandboxNotFound,
    SandboxPolicyViolation,
    SandboxReleaseIncomplete,
)
from harness_core.sandbox.streams import collect, iter_bytes
from harness_core.sandbox.types import (
    GVISOR_SYSCALL_FILTER,
    LIFETIME_LABEL,
    EgressDefault,
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing import write_arms
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run, tar_of, with_mounts

from .conftest import AGENT_LABEL, DOCKER_TEST_IMAGE

if TYPE_CHECKING:
    from app.harness.sandbox.docker_client import DockerClientProvider

    from .conftest import DockerContractEnv

PYTHON_ROOT = Path(__file__).resolve().parents[4]
LEASE = LeaseRef("w", 1)
CAPS = DockerCapabilities(reachable=True, engine_version="28.0.4", runtimes=frozenset({"runc"}), exec_transport="unix")

docker_lane = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]


def _spec(**overrides: Any) -> SandboxSpec:  # noqa: ANN401
    fields: dict[str, Any] = {
        "image_digest": DOCKER_TEST_IMAGE,
        "mode": RuntimeMode.DOCKER_HARDENED,
        "mounts": (MountSpec("/work"),),
        "labels": dict(AGENT_LABEL),
    }
    fields.update(overrides)
    return SandboxSpec(**fields)


def _policy() -> HardeningPolicy:
    return HardeningPolicy(
        user=SANDBOX_USER,
        allowed_networks=frozenset({"egress"}),
        volume_allowed=lambda name: name.startswith("pipeshub-sbx-"),
        allowed_images=frozenset({"pipeshub/sandbox:latest"}),
    )


def _host_config(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    config: dict[str, Any] = dict(
        docker.types.HostConfig(
            "1.48",
            mounts=[{"Type": "volume", "Source": "pipeshub-sbx-1-0", "Target": "/work"}],
            tmpfs={"/tmp": "size=64m"},
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=64,
            mem_limit=256 * 1024 * 1024,
            memswap_limit=256 * 1024 * 1024,
            nano_cpus=500_000_000,
            network_mode="none",
            init=True,
        )
    )
    config.update(overrides)
    return config


class TestHardeningPolicy:
    """DB-2: pure, no daemon."""

    def test_the_broker_shape_passes(self) -> None:
        assert _policy().violations(image="pipeshub/sandbox:latest", user=SANDBOX_USER, host_config=_host_config()) == []

    @pytest.mark.parametrize(
        ("change", "fragment"),
        [
            ({"Privileged": True}, "privileged"),
            ({"Binds": ["/:/host"]}, "Binds"),
            ({"PortBindings": {"80/tcp": [{"HostPort": "80"}]}}, "PortBindings"),
            ({"PublishAllPorts": True}, "PublishAllPorts"),
            ({"PidMode": "host"}, "PidMode"),
            ({"UsernsMode": "host"}, "UsernsMode"),
            ({"CapAdd": ["SYS_ADMIN"]}, "CapAdd"),
            ({"CapDrop": []}, "CapDrop"),
            ({"SecurityOpt": ["seccomp=unconfined"]}, "SecurityOpt"),
            ({"ReadonlyRootfs": False}, "writable"),
            ({"Runtime": "kata"}, "runtime"),
            ({"NetworkMode": "host"}, "network"),
            ({"NetworkMode": "bridge"}, "network"),
            ({"PidsLimit": None}, "PidsLimit"),
            ({"Memory": 0}, "Memory"),
            ({"MemorySwap": -1}, "swap"),
            ({"Devices": [{"PathOnHost": "/dev/kvm"}]}, "Devices"),
            ({"Mounts": [{"Type": "bind", "Source": "/", "Target": "/host"}]}, "mount"),
            ({"Mounts": [{"Type": "volume", "Source": "someone-elses", "Target": "/x"}]}, "mount"),
            ({"RestartPolicy": {"Name": "always"}}, "restart"),
            ({"Annotations": {"dev.gvisor.flag.network": "host"}}, "Annotations"),
            ({"Annotations": {"dev.gvisor.spec.mount.work.share": "shared"}}, "Annotations"),
            ({"Runtime": "runsc-debug"}, "runtime"),
        ],
    )
    def test_each_weakening_is_refused(self, change: dict[str, Any], fragment: str) -> None:
        with pytest.raises(SandboxPolicyViolation) as refused:
            _policy().check(image="pipeshub/sandbox:latest", user=SANDBOX_USER, host_config=_host_config(**change))
        assert fragment in str(refused.value)
        assert refused.value.reason == "hardening_policy"

    @pytest.mark.parametrize("user", ["root", "0", "0:0", ""])
    def test_root_is_refused(self, user: str) -> None:
        with pytest.raises(SandboxPolicyViolation):
            _policy().check(image="pipeshub/sandbox:latest", user=user, host_config=_host_config())

    def test_an_image_outside_the_allowlist_is_refused(self) -> None:
        with pytest.raises(SandboxPolicyViolation, match="image"):
            _policy().check(image="alpine:latest", user=SANDBOX_USER, host_config=_host_config())

    @pytest.mark.parametrize(
        ("a", "b"),
        [("pipeshub/sandbox", "docker.io/pipeshub/sandbox:latest"), ("python", "library/python:latest")],
    )
    def test_image_spellings_normalise(self, a: str, b: str) -> None:
        assert normalize_image(a) == normalize_image(b)


class _ProbeClient:
    """What ``probe`` reads of a daemon: its version and its runtimes."""

    api = type("Api", (), {"api_version": "1.48"})()

    def __init__(self, runtimes: dict[str, Any]) -> None:
        self._runtimes = runtimes

    def version(self) -> dict[str, str]:
        return {"Version": "29.8.1"}

    def info(self) -> dict[str, Any]:
        return {"Runtimes": self._runtimes}


class TestGvisorConfiguration:
    """GVS-1..GVS-3 (C6.05, R31-GVISO): nothing a spec says reaches runsc's configuration, and a daemon whose
    runsc entry weakens gVisor is not offered as gVisor."""

    @pytest.mark.parametrize(
        "key", ["dev.gvisor.flag.network", "dev.gvisor.spec.mount.work.share", "DEV.GVISOR.flag.net-raw",
                "io.katacontainers.config.hypervisor.kernel_params"],
    )
    async def test_gvs1_a_spec_label_in_a_runtime_namespace_is_refused_before_any_create(self, key: str) -> None:
        provider = _FakeProvider()
        broker = _broker_on(provider, DockerCapabilities(True, "28.0.4", frozenset({"runc", "runsc"}), "unix"))
        with pytest.raises(InvalidSandboxSpec, match="reserved namespace") as refused:
            await broker.acquire(_spec(mode=RuntimeMode.GVISOR, labels={key: "host"}), lease=LEASE, idempotency_key="k")
        assert refused.value.reason == "invalid_spec"
        assert provider.client.api.host_configs == []

    async def test_gvs1_the_create_request_carries_no_annotations(self) -> None:
        provider = _FakeProvider()
        broker = _broker_on(provider, DockerCapabilities(True, "28.0.4", frozenset({"runc", "runsc"}), "unix"))
        with pytest.raises(BrokerUnavailable):
            await broker.acquire(_spec(mode=RuntimeMode.GVISOR), lease=LEASE, idempotency_key="k")
        [host_config] = provider.client.api.host_configs
        assert host_config["Runtime"] == "runsc" and "Annotations" not in host_config
        assert host_config["NetworkMode"] == "none"

    @pytest.mark.parametrize(
        ("args", "found"),
        [
            (["--allow-flag-override"], ("--allow-flag-override",)),
            (["--allow-flag-override=true"], ("--allow-flag-override=true",)),
            (["--network=host"], ("--network=host",)),
            (["--network", "host"], ("--network=host",)),
            (["-network=HOST"], ("--network=host",)),
            (["--net-raw"], ("--net-raw",)),
            (["--host-uds=all"], ("--host-uds=all",)),
            (["--host-fifo=open"], ("--host-fifo=open",)),
            (["--TESTONLY-unsafe-nonroot"], ("--TESTONLY-unsafe-nonroot",)),
            (["--platform=systrap", "--network=sandbox", "--host-uds=none", "--net-raw=false"], ()),
            (None, ()),
            ([], ()),
        ],
    )
    def test_gvs2_unsafe_runsc_arguments_are_named(self, args: list[str] | None, found: tuple[str, ...]) -> None:
        assert unsafe_runsc_args(args) == found

    async def test_gvs4_gvisor_limits_processes_in_the_sentry_and_the_host_cgroup_above_it(self) -> None:
        """Under gVisor the host's pids cgroup counts the Sentry's threads and stub processes: at the spec's 64 it
        let 16 processes start (R31-GVISO). runc keeps the cgroup at the spec and no NPROC (a host-wide count)."""
        provider = _FakeProvider()
        broker = _broker_on(provider, DockerCapabilities(True, "28.0.4", frozenset({"runc", "runsc"}), "unix"))
        resources = Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64)
        for mode in (RuntimeMode.GVISOR, RuntimeMode.DOCKER_HARDENED):
            with pytest.raises(BrokerUnavailable):
                await broker.acquire(_spec(mode=mode, resources=resources), lease=LEASE, idempotency_key=mode.value)
        gvisor, runc = provider.client.api.host_configs
        assert gvisor["PidsLimit"] == GVISOR_HOST_PIDS_BASE + GVISOR_HOST_PIDS_PER_TASK * 64
        assert {"Name": "nproc", "Soft": 64, "Hard": 64} in gvisor["Ulimits"]
        assert runc["PidsLimit"] == 64
        assert all(limit["Name"] != "nproc" for limit in runc["Ulimits"])

    async def test_gvs5_a_withheld_runsc_is_not_offered_and_the_detail_says_so(self) -> None:
        provider = _FakeProvider()
        provider.client = _ProbeClient({"runc": {"path": "runc"}, "runsc": {"path": "/usr/bin/runsc"}})
        caps = await probe(provider, withheld=frozenset({"runsc"}))  # type: ignore[arg-type]
        assert caps.modes() == frozenset({RuntimeMode.DOCKER_HARDENED})
        assert caps.withheld == frozenset({"runsc"}) and caps.detail()["withheld"] == "runsc"
        offered = await probe(provider)  # type: ignore[arg-type]
        assert RuntimeMode.GVISOR in offered.modes() and "withheld" not in offered.detail()

    async def test_gvs3_a_daemon_whose_runsc_allows_flag_overrides_offers_no_gvisor(self) -> None:
        provider = _FakeProvider()
        provider.client = _ProbeClient(
            {"runc": {"path": "runc"}, "runsc": {"path": "/usr/bin/runsc", "runtimeArgs": ["--allow-flag-override"]}}
        )
        caps = await probe(provider)  # type: ignore[arg-type]
        assert caps.reachable and "runsc" in caps.runtimes
        assert caps.unsafe_runsc == ("--allow-flag-override",)
        assert RuntimeMode.GVISOR not in caps.modes()
        assert caps.detail()["runscRefused"] == "--allow-flag-override"
        broker = _broker_on(_FakeProvider(), caps)
        with pytest.raises(IsolationUnavailable):
            await broker.acquire(_spec(mode=RuntimeMode.GVISOR), lease=LEASE, idempotency_key="k")

    @pytest.mark.parametrize(
        ("args", "on"),
        [
            (["--oci-seccomp"], True),
            (["-oci-seccomp"], True),
            (["--oci-seccomp=true"], True),
            (["--OCI-SECCOMP=T"], True),
            (["--oci-seccomp=1", "--platform=systrap"], True),
            (["--oci-seccomp", "--oci-seccomp=false"], False),
            (["--oci-seccomp=0"], False),
            (["--platform=systrap"], False),
            (None, False),
            ([], False),
        ],
    )
    def test_gvs9_the_syscall_filter_is_read_from_the_runsc_arguments(self, args: list[str] | None, on: bool) -> None:
        """R32-SECCOMP: Go's flag forms, the last occurrence winning."""
        assert runsc_oci_seccomp(args) is on

    @pytest.mark.parametrize(("args", "said"), [(["--oci-seccomp"], "on"), ([], "off")])
    async def test_gvs10_the_health_says_whether_gvisor_filters_its_guest_and_warns_when_not(
        self, args: list[str], said: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Off is still gVisor (offered, not refused: an install registered by ``runsc install`` keeps its engines),
        with the field the admin view warns from and a broker warning naming the fix."""
        provider = _FakeProvider()
        provider.client = _ProbeClient({"runc": {"path": "runc"}, "runsc": {"path": "/usr/bin/runsc", "runtimeArgs": args}})
        caps = await probe(provider)  # type: ignore[arg-type]
        assert RuntimeMode.GVISOR in caps.modes()
        assert caps.detail()[GVISOR_SYSCALL_FILTER] == said
        caplog.set_level(logging.WARNING, logger="app.harness.sandbox.broker")
        # Started before runsc was registered: the next health finds it, says so and warns once.
        broker = _broker_on(provider, DockerCapabilities(True, "29.8.1", frozenset({"runc"}), "unix"))
        assert (await broker.health()).detail[GVISOR_SYSCALL_FILTER] == said
        assert (await broker.health()).detail[GVISOR_SYSCALL_FILTER] == said
        warned = [r for r in caplog.records if r.getMessage().startswith("broker.gvisor_syscall_filter_off")]
        assert len(warned) == (said == "off")

    async def test_gvs10_a_daemon_without_gvisor_says_nothing_of_its_filter(self) -> None:
        provider = _FakeProvider()
        provider.client = _ProbeClient({"runc": {"path": "runc"}})
        assert GVISOR_SYSCALL_FILTER not in (await probe(provider)).detail()  # type: ignore[arg-type]


class _FakeApi:
    """Records the create request and stops there (DB-4)."""

    def __init__(self) -> None:
        self.host_configs: list[dict[str, Any]] = []
        self.api_version = "1.48"

    def create_host_config(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return dict(docker.types.HostConfig("1.48", **kwargs))

    def create_volume(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {}

    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.host_configs.append(kwargs["host_config"])
        raise RuntimeError("stop after recording")

    def volumes(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"Volumes": []}

    def containers(self, **_: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return []


class _FakeProvider:
    def __init__(self) -> None:
        self.client = type("Client", (), {"api": _FakeApi()})()

    async def run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return fn(*args, **kwargs)

    async def ensure_image(self, _: str) -> bool:
        return True

    async def image_labels(self, _: str) -> dict[str, str]:
        return {}

    async def ensure_egress_network(self, name: str) -> str:
        return name


def _broker_on(provider: object, caps: DockerCapabilities = CAPS, **kwargs: Any) -> DockerSandboxBroker:  # noqa: ANN401
    return DockerSandboxBroker(provider=provider, clock=ManualClock(), ids=RandomIds(), capabilities=caps, **kwargs)  # type: ignore[arg-type]


class TestModesAndRefusals:
    async def test_gvisor_is_not_offered_without_runsc_and_is_refused_once_with_a_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broker = _broker_on(_FakeProvider())
        assert broker.modes() == frozenset({RuntimeMode.DOCKER_HARDENED})
        caplog.set_level(logging.WARNING, logger="app.harness.sandbox.broker")
        with pytest.raises(IsolationUnavailable) as refused:
            await broker.acquire(_spec(mode=RuntimeMode.GVISOR, labels={"runId": "r-7"}), lease=LEASE, idempotency_key="k")
        assert refused.value.reason == "mode_unavailable"
        [line] = [r for r in caplog.records if r.getMessage().startswith("sandbox.refused")]
        assert "reason=mode_unavailable" in line.getMessage()
        assert "runId=r-7" in line.getMessage()

    async def test_labels_that_clash_with_log_fields_do_not_break_logging(self) -> None:
        with pytest.raises(IsolationUnavailable):
            await _broker_on(_FakeProvider()).acquire(
                _spec(mode=RuntimeMode.GVISOR, labels={"event": "x", "reason": "y", "exc": "z"}),
                lease=LEASE,
                idempotency_key="k",
            )

    async def test_gvisor_uses_the_runsc_runtime(self) -> None:
        provider = _FakeProvider()
        broker = _broker_on(provider, DockerCapabilities(True, "28.0.4", frozenset({"runc", "runsc"}), "unix"))
        assert RuntimeMode.GVISOR in broker.modes()
        with pytest.raises(BrokerUnavailable):
            await broker.acquire(_spec(mode=RuntimeMode.GVISOR), lease=LEASE, idempotency_key="k")
        [host_config] = provider.client.api.host_configs
        assert host_config["Runtime"] == "runsc"

    async def test_runc_sets_no_runtime(self) -> None:
        provider = _FakeProvider()
        with pytest.raises(BrokerUnavailable):
            await _broker_on(provider).acquire(_spec(), lease=LEASE, idempotency_key="k")
        assert "Runtime" not in provider.client.api.host_configs[0]

    async def test_an_unreachable_daemon_offers_no_modes(self) -> None:
        assert _broker_on(_FakeProvider(), DockerCapabilities(reachable=False)).modes() == frozenset()

    async def test_an_egress_allowlist_is_unenforceable_until_the_proxy(self) -> None:
        policy = EgressPolicy(destinations=(EgressDestination("api.example.com"),))
        with pytest.raises(PolicyUnenforceable) as refused:
            await _broker_on(_FakeProvider(), egress_network="egress").acquire(
                _spec(egress=policy), lease=LEASE, idempotency_key="k"
            )
        assert refused.value.reason == "egress_proxy_missing"

    async def test_open_egress_needs_an_egress_network(self) -> None:
        with pytest.raises(PolicyUnenforceable):
            await _broker_on(_FakeProvider()).acquire(
                _spec(egress=EgressPolicy(default=EgressDefault.ALLOW)), lease=LEASE, idempotency_key="k"
            )

    async def test_an_image_outside_the_allowlist_is_refused(self) -> None:
        with pytest.raises(ImageNotAllowed):
            await _broker_on(_FakeProvider(), allowed_images=frozenset({"pipeshub/sandbox:latest"})).acquire(
                _spec(image_digest="alpine:3"), lease=LEASE, idempotency_key="k"
            )

    async def test_a_persistent_mount_needs_a_data_volume(self) -> None:
        with pytest.raises(PolicyUnenforceable):
            await _broker_on(_FakeProvider()).acquire(
                _spec(mounts=(MountSpec("/ws", source="org/s/ws"),)), lease=LEASE, idempotency_key="k"
            )


def _containers(provider: DockerClientProvider, namespace: str, **labels: str) -> list[dict[str, Any]]:
    filters = [f"{namespace}.managed=1", *(f"{k}={v}" for k, v in labels.items())]
    return provider.client.api.containers(all=True, filters={"label": filters})


def _volumes(provider: DockerClientProvider, namespace: str, sandbox_id: str) -> list[dict[str, Any]]:
    listed = provider.client.api.volumes(filters={"label": [f"{namespace}.id={sandbox_id}"]}) or {}
    return listed.get("Volumes") or []


@pytest.fixture
def runtime_mode() -> RuntimeMode:
    return RuntimeMode.DOCKER_HARDENED


class TestOnDocker:
    pytestmark = docker_lane

    async def test_the_container_is_hardened(
        self, docker_broker: DockerSandboxBroker, provider: DockerClientProvider, sandbox_env: DockerContractEnv
    ) -> None:
        handle = await docker_broker.acquire(sandbox_env.spec(labels={"runId": "r1"}), lease=LEASE, idempotency_key="k")
        inspected = provider.client.api.inspect_container(handle.container_id)
        host, config = inspected["HostConfig"], inspected["Config"]
        assert host["CapDrop"] == ["ALL"]
        assert host["SecurityOpt"] == ["no-new-privileges:true"]
        assert host["ReadonlyRootfs"] is True
        assert host["Privileged"] is False
        assert host["NetworkMode"] == "none"
        assert not host["PortBindings"]
        assert not host["Binds"]
        assert host["PidsLimit"] == 64
        assert host["Memory"] == host["MemorySwap"] == 256 * 1024 * 1024
        assert host["NanoCpus"] == 500_000_000
        assert config["User"] == SANDBOX_USER
        assert config["Labels"]["runId"] == "r1"
        assert config["Labels"]["pipeshub.agent"] == "P5.1"
        out, _, code = await run(await handle.exec(["id", "-u"], env={}))
        assert (out.strip(), code) == (b"1000", 0)
        _, _, code = await run(await handle.exec(["touch", "/etc/x"], env={}))
        assert code != 0
        await docker_broker.release(handle)

    async def test_release_leaves_no_container_or_volume(
        self, docker_broker: DockerSandboxBroker, provider: DockerClientProvider, sandbox_env: DockerContractEnv, namespace: str
    ) -> None:
        handle = await docker_broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k")
        assert _volumes(provider, namespace, handle.id)
        await docker_broker.release(handle)
        assert _containers(provider, namespace) == []
        assert _volumes(provider, namespace, handle.id) == []

    async def test_db14_a_create_the_daemon_finished_after_a_client_timeout_leaves_nothing(
        self,
        docker_broker: DockerSandboxBroker,
        provider: DockerClientProvider,
        sandbox_env: DockerContractEnv,
        namespace: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DB-14 (X3, D1's FB-4): the daemon creates the container but the client times out."""
        import requests

        api = provider.client.api
        real_create = api.create_container

        def created_then_timed_out(*args: object, **kwargs: object) -> object:
            _ = real_create(*args, **kwargs)
            raise requests.exceptions.ReadTimeout("simulated: the response never arrived")

        monkeypatch.setattr(api, "create_container", created_then_timed_out)
        with pytest.raises(BrokerUnavailable):
            _ = await docker_broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-timeout")
        assert _containers(provider, namespace) == []
        left = api.volumes(filters={"label": [f"{namespace}.managed=1"]}) or {}
        assert (left.get("Volumes") or []) == []

    async def test_a_restarted_broker_adopts_the_running_sandbox(
        self, docker_broker: DockerSandboxBroker, provider: DockerClientProvider, clock: ManualClock,
        sandbox_env: DockerContractEnv, namespace: str, volume_prefix: str,
    ) -> None:
        first = await docker_broker.acquire(sandbox_env.spec(), lease=LeaseRef("w", 2), idempotency_key="k-adopt")
        restarted = await DockerSandboxBroker.create(
            provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix
        )
        adopted = await restarted.acquire(sandbox_env.spec(), lease=LeaseRef("w", 3), idempotency_key="k-adopt")
        assert adopted.id == first.id
        out, _, _ = await run(await adopted.exec(sandbox_env.argv_print("still here", "", 0), env={}))
        assert out == b"still here"
        await restarted.release(adopted)
        assert _containers(provider, namespace) == []

    async def test_a_warm_sandbox_is_swept_after_its_window(
        self, docker_broker: DockerSandboxBroker, provider: DockerClientProvider, clock: ManualClock,
        sandbox_env: DockerContractEnv, namespace: str,
    ) -> None:
        handle = await docker_broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k")
        await docker_broker.release(handle, keep_warm_s=60)
        assert await docker_broker.sweep_warm() == 0
        _ = clock.advance(61)
        assert await docker_broker.sweep_warm() == 1
        assert _containers(provider, namespace) == []

    async def test_open_egress_joins_only_the_egress_network(
        self, provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
        cleanup_namespace: None,
    ) -> None:
        network = f"{volume_prefix}-egress"
        broker = await DockerSandboxBroker.create(
            provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace,
            volume_prefix=volume_prefix, egress_network=network, helper_image=DOCKER_TEST_IMAGE,
        )
        try:
            handle = await broker.acquire(
                _spec(egress=EgressPolicy(default=EgressDefault.ALLOW)), lease=LEASE, idempotency_key="k"
            )
            networks = provider.client.api.inspect_container(handle.container_id)["NetworkSettings"]["Networks"]
            assert set(networks) == {network}
            await broker.release(handle)
        finally:
            for found in provider.client.api.networks(names=[network]):
                provider.client.api.remove_network(found["Id"])

    async def test_capability_changes_are_logged(
        self, provider: DockerClientProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        broker = _broker_on(provider, DockerCapabilities(reachable=False))
        caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
        health = await broker.health()
        assert health.ok
        assert RuntimeMode.DOCKER_HARDENED in health.modes
        assert any(r.getMessage().startswith("broker.capabilities_changed") for r in caplog.records)


@pytest.fixture
def data_volume(provider: DockerClientProvider, volume_prefix: str) -> Any:  # noqa: ANN401
    name = f"{volume_prefix}-data"
    provider.client.api.create_volume(name=name, labels=dict(AGENT_LABEL))
    yield name
    provider.client.api.remove_volume(name, force=True)


def _read_store(provider: DockerClientProvider, volume: str, path: str) -> bytes:
    return provider.client.containers.run(
        DOCKER_TEST_IMAGE, ["cat", f"/data/{path}"], remove=True, network_mode="none",
        labels=dict(AGENT_LABEL), mounts=[docker.types.Mount("/data", volume, type="volume", read_only=True)],
    )


@pytest.mark.docker
@pytest.mark.parametrize("copy_mounts", [False, True], ids=["subpath", "tar-copy"])
async def test_a_persistent_mount_lands_in_the_store(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    cleanup_namespace: None, data_volume: str, copy_mounts: bool,
) -> None:
    """DB-5 / DB-6: the same bytes in the store whether the engine mounts a subpath or the broker copies."""
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
        data_volume=data_volume, copy_persistent_mounts=copy_mounts,
    )
    source = f"org-{uuid.uuid4().hex[:6]}/session-1/ws"
    spec = _spec(mounts=(MountSpec("/ws", source=source),))
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key="k1")
    mounts = provider.client.api.inspect_container(handle.container_id)["HostConfig"]["Mounts"]
    subpaths = [m.get("VolumeOptions", {}).get("Subpath") for m in mounts if m.get("Source") == data_volume]
    assert subpaths == ([] if copy_mounts else [source])
    _, err, code = await run(await handle.exec(["sh", "-c", "echo first > /ws/a.txt"], env={}))
    assert code == 0, err
    await broker.release(handle)
    assert _read_store(provider, data_volume, f"{source}/a.txt") == b"first\n"

    again = await broker.acquire(spec, lease=LEASE, idempotency_key="k2")
    out, _, _ = await run(await again.exec(["cat", "/ws/a.txt"], env={}))
    assert out == b"first\n"
    assert await collect(await again.get_tar("/ws/a.txt"))
    await broker.release(again)


def _on_store(provider: DockerClientProvider, volume: str, command: str) -> None:
    provider.client.containers.run(
        DOCKER_TEST_IMAGE, ["sh", "-c", command], remove=True, network_mode="none", user="0",
        labels=dict(AGENT_LABEL), mounts=[docker.types.Mount("/data", volume, type="volume")],
    )


@pytest.mark.docker
async def test_mcpleak_a_crashed_holders_sandbox_is_reaped_by_the_broker_past_its_lifetime(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str, cleanup_namespace: None,
) -> None:
    """A holder that died never releases: a restarted broker (empty ledger) removes its sandbox once the sandbox's
    own lifetime label has passed, keeps a younger one and one without the label, and is idempotent."""
    crashed = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
    )
    short = await crashed.acquire(_spec(labels={**AGENT_LABEL, LIFETIME_LABEL: "60"}), lease=LEASE, idempotency_key="a")
    long = await crashed.acquire(_spec(labels={**AGENT_LABEL, LIFETIME_LABEL: "600"}), lease=LEASE, idempotency_key="b")
    unlabelled = await crashed.acquire(_spec(), lease=LEASE, idempotency_key="c")
    name = provider.client.api.inspect_container(short.container_id)["Name"]
    assert name == f"/{volume_prefix}-sbx-{short.id}"
    restarted = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
    )

    assert await restarted.reap_expired() == 0
    clock.advance(61)
    assert await restarted.reap_expired() == 1
    assert await restarted.reap_expired() == 0
    with pytest.raises(docker.errors.NotFound):
        provider.client.api.inspect_container(short.container_id)
    for kept in (long, unlabelled):
        assert provider.client.api.inspect_container(kept.container_id)["State"]["Running"]


@pytest.mark.docker
async def test_m4_1_a_copy_out_that_fails_keeps_the_sandbox_until_the_warm_sweep_saves_it(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    cleanup_namespace: None, data_volume: str,
) -> None:
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
        data_volume=data_volume, copy_persistent_mounts=True,
    )
    source = f"org-{uuid.uuid4().hex[:6]}/session-1/ws"
    handle = await broker.acquire(_spec(mounts=(MountSpec("/ws", source=source),)), lease=LEASE, idempotency_key="k1")
    _, err, code = await run(await handle.exec(["sh", "-c", "echo kept > /ws/a.txt"], env={}))
    assert code == 0, err
    # The persistent folder is gone when the release copies back (a store hiccup), so the copy fails.
    _on_store(provider, data_volume, f"rm -rf /data/{source}")

    with pytest.raises(SandboxReleaseIncomplete):
        await broker.release(handle)
    assert provider.client.api.inspect_container(handle.container_id)["State"]["Running"]

    _on_store(provider, data_volume, f"mkdir -p /data/{source} && chown -R 1000:1000 /data/org-*")
    clock.advance(1)
    assert await broker.sweep_warm() == 1
    assert _read_store(provider, data_volume, f"{source}/a.txt") == b"kept\n"
    with pytest.raises(docker.errors.NotFound):
        provider.client.api.inspect_container(handle.container_id)


@pytest.mark.docker
async def test_signalling_a_process_of_a_killed_sandbox_says_the_sandbox_is_gone(
    docker_broker: DockerSandboxBroker, provider: DockerClientProvider, sandbox_env: DockerContractEnv,
) -> None:
    """E1 G4: an engine interrupted after its sandbox was killed must read as a gone sandbox, not a 409."""
    handle = await docker_broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-signal-gone")
    try:
        process = await handle.exec(sandbox_env.argv_sleep(), env={})
        provider.client.api.kill(handle.container_id)
        with pytest.raises(SandboxNotFound):
            await process.signal(signal.SIGTERM)
    finally:
        await docker_broker.release(handle)


@pytest.mark.docker
async def test_reading_the_workspace_of_a_killed_sandbox_says_the_sandbox_is_gone(
    docker_broker: DockerSandboxBroker, provider: DockerClientProvider, sandbox_env: DockerContractEnv,
) -> None:
    """E1b (found by the Docker wave 2 slice): a killed sandbox's scratch workspace is gone, and its archive read
    as an empty folder, so the turn's last save committed the deletion of every file. It must read as gone."""
    spec = with_mounts(sandbox_env.spec(), MountSpec("/ws"))
    handle = await docker_broker.acquire(spec, lease=LEASE, idempotency_key="k-tar-gone")
    try:
        _, err, code = await run(await handle.exec(["sh", "-c", "echo kept > /ws/a.txt"], env={}))
        assert code == 0, err
        assert await collect(await handle.get_tar("/ws"))
        provider.client.api.kill(handle.container_id)
        with pytest.raises(SandboxNotFound):
            _ = await collect(await handle.get_tar("/ws"))
    finally:
        await docker_broker.release(handle)


@pytest.mark.docker
async def test_c2_19_the_install_ceiling_counts_every_live_sandbox_and_frees_on_release(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str, cleanup_namespace: None,
) -> None:
    """SBX-21 on the broker: at the ceiling a new sandbox waits (BrokerUnavailable), the held one is still reachable
    by its key, and a release makes room. A second broker of the namespace counts the first one's sandbox."""
    from .conftest import DockerContractEnv

    def make() -> Any:  # noqa: ANN401
        return DockerSandboxBroker.create(
            provider=provider,
            clock=clock,
            ids=RandomIds(),
            allowed_images=frozenset({DOCKER_TEST_IMAGE}),
            label_namespace=namespace,
            volume_prefix=volume_prefix,
            max_sandboxes=1,
        )

    broker, other = await make(), await make()
    spec = DockerContractEnv(clock, RuntimeMode.DOCKER_HARDENED, namespace).spec()
    held = await broker.acquire(spec, lease=LEASE, idempotency_key="k-cap-1")
    with pytest.raises(BrokerUnavailable, match="1 of 1"):
        _ = await broker.acquire(spec, lease=LEASE, idempotency_key="k-cap-2")
    with pytest.raises(BrokerUnavailable, match="1 of 1"):
        _ = await other.acquire(spec, lease=LEASE, idempotency_key="k-cap-3")
    again = await broker.acquire(spec, lease=LEASE, idempotency_key="k-cap-1")
    assert again.id == held.id
    await broker.release(held)
    second = await other.acquire(spec, lease=LEASE, idempotency_key="k-cap-2")
    await other.release(second)


@pytest.mark.docker
async def test_dk3_seeding_leaves_no_helper_and_release_leaves_nothing(
    docker_broker: DockerSandboxBroker, provider: DockerClientProvider, sandbox_env: DockerContractEnv, namespace: str,
) -> None:
    """Seeds go through the sandbox's one seeder (PF-01), never started; nothing else is left beside the sandbox."""
    spec = with_mounts(sandbox_env.spec(), MountSpec("/skills", read_only=True))
    handle = await docker_broker.acquire(spec, lease=LEASE, idempotency_key="k-seed")
    await handle.seed_tar("/skills", iter_bytes(tar_of({"s/SKILL.md": b"# s"})))
    await handle.seed_tar("/skills", iter_bytes(tar_of({"t/SKILL.md": b"# t"})))
    assert sorted((c["Labels"][f"{namespace}.role"], c["State"]) for c in _containers(provider, namespace)) == [
        ("sandbox", "running"), ("seeder", "created")
    ]
    await docker_broker.release(handle)
    assert _containers(provider, namespace) == []
    assert _volumes(provider, namespace, handle.id) == []


@pytest.mark.docker
async def test_wp59_a_read_only_scratch_mount_is_roots_alone_and_a_writable_one_stays_sticky(
    docker_broker: DockerSandboxBroker, sandbox_env: DockerContractEnv
) -> None:
    """A read-only scratch mount has no writer inside, so its root is 0755 root's: an agent that trusts a config
    only in folders nobody else can write (Gemini CLI's system settings) trusts one seeded there. A writable one
    keeps root's sticky 1777 (SEC1)."""
    spec = with_mounts(sandbox_env.spec(), MountSpec("/pxh-sealed", read_only=True), MountSpec("/scratch"))
    handle = await docker_broker.acquire(spec, lease=LEASE, idempotency_key="k-wp59-modes")
    await handle.seed_tar("/pxh-sealed", iter_bytes(tar_of({"agent/settings.json": b"{}"})))
    out, _, code = await run(await handle.exec(["stat", "-c", "%u %a %n", "/pxh-sealed", "/scratch"], env={}))
    assert code == 0, out
    assert out.decode().splitlines() == ["0 755 /pxh-sealed", "0 1777 /scratch"]
    _, _, written = await run(await handle.exec(["sh", "-c", "echo x > /pxh-sealed/x"], env={}))
    assert written != 0
    await docker_broker.release(handle)


@pytest.mark.docker
async def test_a_restarted_broker_seeds_the_adopted_sandbox(
    docker_broker: DockerSandboxBroker, provider: DockerClientProvider, clock: ManualClock,
    sandbox_env: DockerContractEnv, namespace: str, volume_prefix: str,
) -> None:
    spec = with_mounts(sandbox_env.spec(), MountSpec("/skills", read_only=True))
    first = await docker_broker.acquire(spec, lease=LeaseRef("w", 2), idempotency_key="k-adopt-seed")
    restarted = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix
    )
    adopted = await restarted.acquire(spec, lease=LeaseRef("w", 3), idempotency_key="k-adopt-seed")
    assert adopted.id == first.id
    await adopted.seed_tar("/skills", iter_bytes(tar_of({"s/SKILL.md": b"# adopted"})))
    out, _, code = await run(await adopted.exec(["cat", "/skills/s/SKILL.md"], env={}))
    assert (out, code) == (b"# adopted", 0)
    await restarted.release(adopted)


@pytest.mark.docker
async def test_sb24_a_read_only_persistent_mount_is_not_seedable(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    cleanup_namespace: None, data_volume: str,
) -> None:
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
        data_volume=data_volume,
    )
    source = f"org-{uuid.uuid4().hex[:6]}/shared"
    handle = await broker.acquire(
        _spec(mounts=(MountSpec("/work"), MountSpec("/shared", source=source, read_only=True))),
        lease=LEASE, idempotency_key="k-ro-persistent",
    )
    try:
        with pytest.raises(InvalidSandboxRequest):
            await handle.seed_tar("/shared", iter_bytes(tar_of({"x": b"y"})))
    finally:
        await broker.release(handle)


_ORPHAN_CHILD = """
import asyncio, sys
from pathlib import Path
from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.failpoints import registry
from harness_core.ids import RandomIds
from harness_core.clock import SystemClock
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode, SandboxSpec

registry.install_file(Path(sys.argv[1]))

async def main():
    broker = await DockerSandboxBroker.create(
        provider=DockerClientProvider(), clock=SystemClock(), ids=RandomIds(), label_namespace=sys.argv[2],
        volume_prefix=sys.argv[3],
    )
    spec = SandboxSpec(sys.argv[4], RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/work"),),
                       labels={"pipeshub.agent": "P5.1", "runId": "orphaned"})
    await broker.acquire(spec, lease=LeaseRef("w", 1), idempotency_key="k")

asyncio.run(main())
"""


@pytest.mark.docker
async def test_a_broker_killed_mid_provision_leaves_an_orphan_the_reaper_removes(
    provider: DockerClientProvider, namespace: str, volume_prefix: str, cleanup_namespace: None, tmp_path: Path,
) -> None:
    """DB-9: failpoint ``broker.provision.after_create_before_ref`` with kill -9, then ``reap`` by label."""
    arms = tmp_path / "failpoints.json"
    write_arms(arms, {"broker.provision.after_create_before_ref": Arm(Kill9())})
    child = subprocess.run(
        [sys.executable, "-c", _ORPHAN_CHILD, str(arms), namespace, volume_prefix, DOCKER_TEST_IMAGE],
        cwd=PYTHON_ROOT, capture_output=True, text=True, timeout=240, check=False,
    )
    assert child.returncode == -signal.SIGKILL, child.stderr
    [orphan] = _containers(provider, namespace, runId="orphaned")
    sandbox_id = orphan["Labels"][f"{namespace}.id"]
    assert _volumes(provider, namespace, sandbox_id)
    sweeper = await DockerSandboxBroker.create(
        provider=provider, clock=SystemClock(), ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix
    )
    assert await sweeper.reap(labels={"runId": "orphaned"}, older_than_s=0) == 1
    assert _containers(provider, namespace, runId="orphaned") == []
    assert _volumes(provider, namespace, sandbox_id) == []


pytestmark = [pytest.mark.timeout(300, method="thread")]

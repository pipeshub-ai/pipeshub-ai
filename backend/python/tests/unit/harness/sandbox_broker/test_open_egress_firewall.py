"""PR1-S1: open egress (``EgressDefault.ALLOW``) on the Docker broker is address-filtered like main's docker backend.

Main's #3780 firewalls every container that joins the egress bridge (private,
link-local/metadata and bridge addresses rejected) from inside the container.
A broker sandbox has no capabilities and a read-only root, so it cannot do
that itself: the broker, which holds the socket, runs a one-shot helper in
the sandbox's network namespace that installs the same rules before the
sandbox is handed out, and refuses the sandbox when it cannot.
"""

from __future__ import annotations

import ipaddress
import logging
import uuid
from typing import TYPE_CHECKING, Any

import docker
import pytest

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    BLOCKED_CIDRS,
    FIREWALL_UNAVAILABLE_EXIT_CODE,
    build_firewall_rules_script,
)
from app.harness.sandbox.docker_broker import (
    EGRESS_FIREWALL_UNAVAILABLE,
    DockerCapabilities,
    DockerSandboxBroker,
)
from app.harness.sandbox.docker_policy import HardeningPolicy
from harness_core.ids import RandomIds
from harness_core.sandbox.errors import PolicyUnenforceable, SandboxPolicyViolation
from harness_core.sandbox.types import (
    EgressDefault,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

if TYPE_CHECKING:
    from app.harness.sandbox.docker_client import DockerClientProvider

LEASE = LeaseRef("holder-1", 1)
EGRESS_SUBNET = "172.30.7.0/24"
TOKEN = "0123456789abcdef0123456789abcdef"


def _spec(**overrides: Any) -> SandboxSpec:  # noqa: ANN401
    values: dict[str, Any] = {
        "image_digest": "pipeshub/sandbox:latest",
        "mode": RuntimeMode.DOCKER_HARDENED,
        "resources": Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        "egress": EgressPolicy(default=EgressDefault.ALLOW),
        "mounts": (MountSpec("/src"),),
    }
    values.update(overrides)
    return SandboxSpec(**values)


class TestRulesScript:
    def test_it_rejects_private_metadata_and_the_bridge_then_exits(self) -> None:
        script = build_firewall_rules_script([EGRESS_SUBNET], ["10.9.0.0/16"], token=TOKEN)
        for cidr in (*BLOCKED_CIDRS, EGRESS_SUBNET, "169.254.0.0/16", "168.63.129.16/32"):
            assert f"-d {cidr} -j REJECT" in script
        assert "r -A OUTPUT -d 10.9.0.0/16 -j ACCEPT" in script
        # Metadata is refused before any operator allowance can admit it.
        assert script.index("169.254.0.0/16 -j REJECT") < script.index("10.9.0.0/16 -j ACCEPT")
        assert "setpriv" not in script
        assert script.rstrip().endswith("exit 0")
        assert f"exit {FIREWALL_UNAVAILABLE_EXIT_CODE}" in script

    def test_a_non_ipv4_network_is_refused(self) -> None:
        with pytest.raises(ValueError, match="IPv4"):
            build_firewall_rules_script(["fd00::/64"], token=TOKEN)


_HELPER_SCRIPT = build_firewall_rules_script(["172.30.0.0/16"], token="cd" * 8)
_HELPER_COMMAND = ["sh", "-c", _HELPER_SCRIPT, "egress-firewall"]


def _helper_config(container_id: str, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    values: dict[str, Any] = {
        "network_mode": f"container:{container_id}",
        "cap_drop": ["ALL"],
        "cap_add": ["NET_ADMIN"],
        "read_only": True,
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 16,
        "mem_limit": 64 * 1024 * 1024,
        "memswap_limit": 64 * 1024 * 1024,
        "nano_cpus": int(0.25 * 1e9),
        "tmpfs": {"/run": "size=1m"},
    }
    values.update(overrides)
    return dict(docker.types.HostConfig("1.48", **values))


class TestFirewallHelperPolicy:
    def _policy(self) -> HardeningPolicy:
        return HardeningPolicy(
            user="1000:1000", allowed_networks=frozenset({"egress"}), volume_allowed=lambda _: False,
            firewall_image="pipeshub/sandbox:latest",
        )

    def test_the_helper_shape_passes(self) -> None:
        self._policy().check_firewall_helper(
            image="pipeshub/sandbox:latest", user="0", host_config=_helper_config("c1"), sandbox_container="c1",
            command=_HELPER_COMMAND, entrypoint=[],
        )

    @pytest.mark.parametrize(
        ("change", "fragment"),
        [
            ({"cap_add": ["NET_ADMIN", "NET_RAW"]}, "CapAdd"),
            ({"cap_add": ["SYS_ADMIN"]}, "CapAdd"),
            ({"network_mode": "container:other"}, "network"),
            ({"network_mode": "host"}, "network"),
            ({"privileged": True}, "privileged"),
            ({"read_only": False}, "writable"),
            ({"pid_mode": "host"}, "PidMode"),
        ],
    )
    def test_anything_beyond_the_rules_is_refused(self, change: dict[str, Any], fragment: str) -> None:
        with pytest.raises(SandboxPolicyViolation, match=fragment):
            self._policy().check_firewall_helper(
                image="pipeshub/sandbox:latest", user="0", host_config=_helper_config("c1", **change), sandbox_container="c1",
                command=_HELPER_COMMAND, entrypoint=[],
            )

    @pytest.mark.parametrize(
        ("command", "entrypoint", "env", "fragment"),
        [
            (["sh", "-c", "set -u\nid", "egress-firewall"], [], None, "rules script"),
            (["sh", "-c", _HELPER_SCRIPT + "id\n", "egress-firewall"], [], None, "rules script"),
            (["sh", "-c", _HELPER_SCRIPT, "egress-firewall"], None, None, "entrypoint"),
            (["sh", "-c", _HELPER_SCRIPT, "egress-firewall"], [], ["PATH=/tmp"], "environment"),
        ],
    )
    def test_only_the_rules_script_runs(self, command: object, entrypoint: object, env: object, fragment: str) -> None:
        with pytest.raises(SandboxPolicyViolation, match=fragment):
            self._policy().check_firewall_helper(
                image="pipeshub/sandbox:latest", user="0", host_config=_helper_config("c1"), sandbox_container="c1",
                command=command, entrypoint=entrypoint, env=env,
            )

    @pytest.mark.parametrize("image", ["attacker/firewall:latest", "pipeshub/sandbox:other", "sha256:" + "0" * 64])
    def test_only_the_pinned_image_runs_the_helper(self, image: str) -> None:
        """R44STOP-FU-3: the helper is root with NET_ADMIN; from another image its `sh` and `iptables` are that
        image's."""
        with pytest.raises(SandboxPolicyViolation, match="pinned image"):
            self._policy().check_firewall_helper(
                image=image, user="0", host_config=_helper_config("c1"), sandbox_container="c1",
                command=_HELPER_COMMAND, entrypoint=[],
            )

    def test_with_no_pinned_image_no_helper_runs(self) -> None:
        policy = HardeningPolicy(user="1000:1000", allowed_networks=frozenset({"egress"}), volume_allowed=lambda _: False)
        with pytest.raises(SandboxPolicyViolation, match="pinned image"):
            policy.check_firewall_helper(
                image="pipeshub/sandbox:latest", user="0", host_config=_helper_config("c1"), sandbox_container="c1",
                command=_HELPER_COMMAND, entrypoint=[],
            )

    def test_the_sandbox_policy_still_refuses_net_admin(self) -> None:
        with pytest.raises(SandboxPolicyViolation, match="CapAdd"):
            self._policy().check(image="pipeshub/sandbox:latest", user="1000:1000", host_config=_helper_config("c1", network_mode="egress"))


class _Api:
    """Creates, starts and waits like the daemon; the firewall helper's exit code is scripted."""

    def __init__(self, helper_exit: int = 0, helper_stderr: bytes = b"") -> None:
        self.api_version = "1.48"
        self.created: list[dict[str, Any]] = []
        self.removed: list[str] = []
        self.helper_exit = helper_exit
        self.helper_stderr = helper_stderr

    def create_host_config(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return dict(docker.types.HostConfig("1.48", **kwargs))

    def create_networking_config(self, *_: Any, **__: Any) -> dict[str, Any]:  # noqa: ANN401
        return {}

    def create_volume(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {}

    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        container_id = f"c{len(self.created)}"
        self.created.append({"Id": container_id, "image": image, **kwargs})
        return {"Id": container_id}

    def start(self, _: str) -> None:
        return None

    def wait(self, container_id: str, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"StatusCode": self.helper_exit}

    def logs(self, container_id: str, **_: Any) -> bytes:  # noqa: ANN401
        return self.helper_stderr

    def remove_container(self, container_id: str, **_: Any) -> None:  # noqa: ANN401
        self.removed.append(container_id)

    def remove_volume(self, *_: Any, **__: Any) -> None:  # noqa: ANN401
        return None

    def volumes(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"Volumes": []}

    def containers(self, **_: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return []


class _Provider:
    def __init__(self, api: _Api, cidrs: list[str] | None = None) -> None:
        self.client = type("Client", (), {"api": api})()
        self.cidrs = [EGRESS_SUBNET] if cidrs is None else cidrs

    async def run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return fn(*args, **kwargs)

    async def ensure_image(self, _: str) -> bool:
        return True

    async def image_labels(self, _: str) -> dict[str, str]:
        return {}

    async def ensure_egress_network(self, name: str) -> str:
        return name

    async def egress_network_cidrs(self, _: str) -> list[str]:
        return list(self.cidrs)


CAPS = DockerCapabilities(True, "28.0.4", frozenset({"runc"}), "unix")


def _broker(provider: _Provider, caps: DockerCapabilities = CAPS, **kwargs: Any) -> DockerSandboxBroker:  # noqa: ANN401
    return DockerSandboxBroker(
        provider=provider,  # type: ignore[arg-type]
        clock=ManualClock(),
        ids=RandomIds(),
        capabilities=caps,
        egress_network="pipeshub_sandbox_egress",
        **{"helper_image": "pipeshub/sandbox:latest", **kwargs},
    )


class TestOpenEgressOnTheBroker:
    async def test_the_rules_are_installed_in_the_sandbox_namespace_before_it_is_handed_out(self) -> None:
        api = _Api()
        handle = await _broker(_Provider(api), egress_allow_cidrs=("10.9.0.0/16",)).acquire(
            _spec(), lease=LEASE, idempotency_key="k"
        )
        sandbox, helper = api.created
        assert sandbox["host_config"]["NetworkMode"] == "pipeshub_sandbox_egress"
        assert sandbox["host_config"].get("CapAdd") in (None, [])
        hc = helper["host_config"]
        assert hc["NetworkMode"] == f"container:{sandbox['Id']}"
        assert hc["CapAdd"] == ["NET_ADMIN"]
        assert hc["CapDrop"] == ["ALL"]
        assert helper["user"] == "0"
        assert helper["image"] == "pipeshub/sandbox:latest"
        script = helper["command"][2]
        assert f"-d {EGRESS_SUBNET} -j REJECT" in script
        assert "-d 169.254.0.0/16 -j REJECT" in script
        assert "-d 10.9.0.0/16 -j ACCEPT" in script
        assert helper["Id"] in api.removed
        assert handle.container_id == sandbox["Id"]

    async def test_the_helper_runs_from_the_deployments_image_never_the_runs(self) -> None:
        """R44STOP-FU-3: a run whose image is not the deployment's sandbox image (an engine's, or one a foothold made)
        still gets its firewall from the pinned image."""
        api = _Api()
        await _broker(_Provider(api)).acquire(_spec(image_digest="engine/opencode:1"), lease=LEASE, idempotency_key="k")
        sandbox, helper = api.created
        assert sandbox["image"] == "engine/opencode:1"
        assert helper["image"] == "pipeshub/sandbox:latest"

    async def test_a_broker_with_no_firewall_image_refuses_open_egress(self) -> None:
        api = _Api()
        with pytest.raises(PolicyUnenforceable, match="no egress firewall image") as refused:
            await _broker(_Provider(api), helper_image=None).acquire(_spec(), lease=LEASE, idempotency_key="k")
        assert refused.value.reason == EGRESS_FIREWALL_UNAVAILABLE

    async def test_denied_egress_runs_no_helper(self) -> None:
        api = _Api()
        await _broker(_Provider(api)).acquire(
            _spec(egress=EgressPolicy(default=EgressDefault.DENY)), lease=LEASE, idempotency_key="k"
        )
        [sandbox] = api.created
        assert sandbox["host_config"]["NetworkMode"] == "none"

    async def test_an_image_without_iptables_is_refused_and_leaves_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        api = _Api(
            helper_exit=FIREWALL_UNAVAILABLE_EXIT_CODE,
            helper_stderr=b"[sandbox-egress] firewall unavailable (tok): no working iptables",
        )
        caplog.set_level(logging.WARNING, logger="app.harness.sandbox.broker")
        with pytest.raises(PolicyUnenforceable) as refused:
            await _broker(_Provider(api)).acquire(_spec(labels={"runId": "r-1"}), lease=LEASE, idempotency_key="k")
        assert refused.value.reason == EGRESS_FIREWALL_UNAVAILABLE
        sandbox, helper = api.created
        assert {sandbox["Id"], helper["Id"]} <= set(api.removed)
        [line] = [r for r in caplog.records if r.getMessage().startswith("sandbox.refused")]
        assert f"reason={EGRESS_FIREWALL_UNAVAILABLE}" in line.getMessage()

    async def test_an_unreadable_bridge_subnet_is_refused_before_anything_is_created(self) -> None:
        api = _Api()
        with pytest.raises(PolicyUnenforceable) as refused:
            await _broker(_Provider(api, cidrs=[])).acquire(_spec(), lease=LEASE, idempotency_key="k")
        assert refused.value.reason == EGRESS_FIREWALL_UNAVAILABLE
        assert api.created == []

    async def test_gvisor_open_egress_is_refused_its_netstack_bypasses_the_namespace_rules(self) -> None:
        api = _Api()
        caps = DockerCapabilities(True, "28.0.4", frozenset({"runc", "runsc"}), "unix")
        with pytest.raises(PolicyUnenforceable) as refused:
            await _broker(_Provider(api), caps).acquire(_spec(mode=RuntimeMode.GVISOR), lease=LEASE, idempotency_key="k")
        assert refused.value.reason == EGRESS_FIREWALL_UNAVAILABLE
        assert api.created == []


@pytest.mark.docker
class TestOnDocker:
    """The rules hold against a real daemon: needs a sandbox image with iptables (deployment/sandbox/Dockerfile)."""

    async def test_open_egress_cannot_reach_the_bridge_gateway_or_metadata(
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
                _spec(image_digest=DOCKER_TEST_IMAGE, labels={"pr1": uuid.uuid4().hex}), lease=LEASE, idempotency_key="k"
            )
            [subnet] = await provider.egress_network_cidrs(network)
            gateway = str(next(ipaddress.ip_network(subnet).hosts()))
            probe = (
                "import socket,sys\n"
                "for host in sys.argv[1:]:\n"
                "    s=socket.socket(); s.settimeout(2)\n"
                "    try:\n"
                "        s.connect((host, 80)); print(host, 'open')\n"
                "    except OSError as e:\n"
                "        print(host, 'refused' if isinstance(e, ConnectionRefusedError) else type(e).__name__)\n"
            )
            process = await handle.exec(["python3", "-c", probe, gateway, "169.254.169.254"], env={})
            from harness_core.sandbox.streams import collect

            out = (await collect(process.stdout)).decode()
            assert await process.wait(timeout_s=30) == 0
            assert "open" not in out, out
            await broker.release(handle)
        finally:
            for found in provider.client.api.networks(names=[network]):
                provider.client.api.remove_network(found["Id"])

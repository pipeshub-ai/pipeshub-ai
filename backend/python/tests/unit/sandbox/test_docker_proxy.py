"""Tests for the sandbox Docker API proxy (app/sandbox/docker_proxy.py)."""

from __future__ import annotations

import asyncio
import gc
import json
import shutil
import tempfile
import urllib.parse
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    CONTAINER_HARDENING,
    DEFAULT_OPEN_EGRESS_POOL,
    ensure_egress_network_sync,
    firewalled_container_kwargs,
    limited,
)
from app.sandbox.docker_proxy import (
    INSTANCE_LABEL,
    MANAGED_LABEL,
    DockerApiPolicy,
    DockerSocketProxy,
    PolicyDenied,
    Route,
    _parse_request_head,
    normalize_image,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

IMAGE = "pipeshubai/pipeshub-sandbox:latest"
EGRESS = "pipeshub_sandbox_egress"


@pytest.fixture
def policy() -> DockerApiPolicy:
    return DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({EGRESS}))


def _sdk_create_body(**kwargs: object) -> dict[str, Any]:
    """The JSON body docker-py sends for ``containers.create(**kwargs)``."""
    docker = pytest.importorskip("docker")
    client = docker.DockerClient(base_url="tcp://127.0.0.1:9", version="1.43")
    captured: dict[str, Any] = {}

    def fake_post_json(url: str, data: object, **_: object) -> MagicMock:
        captured["body"] = data
        return MagicMock()

    try:
        with patch.object(client.api, "_post_json", side_effect=fake_post_json), \
             patch.object(client.api, "_result", return_value={"Id": "abc"}), \
             patch("docker.models.containers.ContainerCollection.get", return_value=MagicMock()):
            client.containers.create(**kwargs)
    finally:
        # Close the client's connection pool; leaked pools otherwise exhaust
        # sockets and break a later socket-based test in this file.
        client.close()
    return json.loads(json.dumps(captured["body"]))


# What legacy run_code sent before SEC-A-04: no pids limit, a writable root, Docker's default capabilities and the
# image's own user. The proxy refuses it now (``test_sec_a_04_…``).
_LEGACY_RUN: dict[str, Any] = {
    "image": IMAGE, "command": ["python3", "/src/main.py"], "environment": {"OUTPUT_DIR": "/output"},
    "working_dir": "/src", "mem_limit": 512 * 1024 * 1024, "nano_cpus": 500_000_000, "pids_limit": 256,
    "network_mode": "none", "network_disabled": True, "read_only": False,
    "tmpfs": {"/tmp": "size=100M"}, "detach": True,
}
# An install with no firewall, as root by the image's default: refused too.
_INSTALL: dict[str, Any] = {
    "image": IMAGE, "command": ["sh", "-c", "pip install x"], "environment": {},
    "mem_limit": 512 * 1024 * 1024, "nano_cpus": 500_000_000, "pids_limit": 256,
    "network": EGRESS, "network_disabled": False, "detach": True,
}
# The kwargs each sandbox code path passes to containers.create now, built with the executors' own helpers, so a
# change there that the proxy would refuse fails here rather than in production.
_RUN: dict[str, Any] = {
    **{k: v for k, v in _LEGACY_RUN.items() if k not in ("read_only", "mem_limit")},
    **limited(512 * 1024 * 1024), **CONTAINER_HARDENING,
}
_FIREWALLED_INSTALL: dict[str, Any] = {
    **{k: v for k, v in _INSTALL.items() if k != "mem_limit"},
    **limited(512 * 1024 * 1024),
    **CONTAINER_HARDENING,
    **firewalled_container_kwargs(
        ["sh", "-c", "pip install x"], network_cidrs=["172.30.0.0/16"],
        allowed_cidrs=["10.20.0.0/16"], token="0" * 32,
    ),
}
_CODING_RUN_NETWORK: dict[str, Any] = {
    "image": IMAGE, "environment": {"OUTPUT_DIR": "/output"},
    "working_dir": "/src", **limited(1 << 20), "nano_cpus": 1, "tmpfs": {"/tmp": "size=100M"},
    "detach": True, "network": EGRESS, "network_disabled": False, **CONTAINER_HARDENING,
    **firewalled_container_kwargs(["python3", "main.py"], network_cidrs=["172.30.0.0/16"], token="1" * 32),
}
_HARDENED_RUN: dict[str, Any] = _RUN
# What create_sandbox_container sends when the daemon enforces a disk quota.
_RUN_WITH_QUOTA: dict[str, Any] = {**_RUN, "storage_opt": {"size": "10g"}}
# What the coding sandbox really sends: its own hardening (init, pids, /tmp, labels), read from the
# code so a field it adds can never again reach the proxy unlisted (PR1: Init was refused).
_CODING_SANDBOX_RUN: dict[str, Any] = {
    **_RUN,
    **DockerCodingSandbox._hardening(  # pyright: ignore[reportPrivateUsage]
        SimpleNamespace(_pids_limit=128, _labels=lambda: {"pipeshub.sandbox": "coding"})  # type: ignore[arg-type]
    ),
}
# Every caller sends the proxy's credential (SEC-A-04).
TOKEN = "t" * 64
# The resource fields a raw create body needs to be admitted.
_LIMITS: dict[str, int] = {"Memory": 512 * 1024 * 1024, "NanoCpus": 500_000_000, "PidsLimit": 256}
# The rest of the sandbox floor (SEC-A-04) a raw create body needs, beside a non-root ``User``.
_FLOOR: dict[str, Any] = {"CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "ReadonlyRootfs": True}
# The smallest raw create the proxy admits: the sandbox floor and nothing else.
_FLOORED_HOST: dict[str, Any] = {
    "NetworkMode": "none", "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "ReadonlyRootfs": True,
    "PidsLimit": 64, "Memory": 1 << 26, "MemorySwap": 1 << 26, "NanoCpus": 100_000_000,
}


def _min_body(**host: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"Image": IMAGE, "User": "sandbox", "HostConfig": {**_FLOORED_HOST, **host}}


class TestNormalizeImage:
    @pytest.mark.parametrize("ref,expected", [
        ("alpine", "alpine:latest"),
        ("docker.io/library/alpine:3", "alpine:3"),
        ("pipeshub/sandbox", "pipeshub/sandbox:latest"),
        ("registry:5000/team/img", "registry:5000/team/img:latest"),
        ("registry:5000/team/img:v1", "registry:5000/team/img:v1"),
        ("img@sha256:abc", "img@sha256:abc"),
    ])
    def test_cases(self, ref: str, expected: str) -> None:
        assert normalize_image(ref) == expected


class TestRouting:
    @pytest.mark.parametrize("method,target", [
        ("GET", "/_ping"), ("HEAD", "/_ping"), ("GET", "/v1.43/version"),
        ("POST", "/v1.43/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox&tag=latest"),
    ])
    def test_plain_allow(self, policy: DockerApiPolicy, method: str, target: str) -> None:
        assert policy.route(method, target).kind == "allow"

    @pytest.mark.parametrize("method,action", [
        ("GET", "json"), ("POST", "start"), ("POST", "wait"), ("POST", "kill"),
        ("GET", "logs"), ("PUT", "archive"), ("GET", "archive"), ("HEAD", "archive"),
    ])
    def test_container_ops_need_ownership(self, policy: DockerApiPolicy, method: str, action: str) -> None:
        route = policy.route(method, f"/v1.43/containers/abc123/{action}?x=1")
        assert route.kind == "container_owned"
        assert route.container == "abc123"

    @pytest.mark.parametrize("method", ["GET", "DELETE"])
    def test_network_inspect_and_remove_need_the_sandbox_network(self, policy: DockerApiPolicy, method: str) -> None:
        route = policy.route(method, "/v1.43/networks/abc123")
        assert route.kind == "network_allowed"
        assert route.network == "abc123"

    def test_delete_needs_ownership(self, policy: DockerApiPolicy) -> None:
        assert policy.route("DELETE", "/v1.43/containers/abc?force=True").kind == "container_owned"

    @pytest.mark.parametrize("method,target", [
        ("GET", "/v1.43/containers/json"),
        ("POST", "/v1.43/containers/abc/exec"),
        ("POST", "/v1.43/containers/abc/attach"),
        ("POST", "/v1.43/containers/abc/update"),
        ("POST", "/v1.43/exec/abc/start"),
        ("POST", "/v1.43/build"),
        ("POST", "/v1.43/volumes/create"),
        ("POST", "/v1.43/info"),
        ("GET", "/v1.43/info/x"),
        ("POST", "/v1.43/swarm/init"),
        ("POST", "/v1.43/plugins/pull"),
        ("POST", "/v1.43/images/create?fromImage=alpine&tag=latest"),
        ("POST", "/v1.43/images/create?fromSrc=-&repo=pipeshubai/pipeshub-sandbox"),
        ("GET", "/v1.43/images/alpine/json"),
        ("DELETE", "/v1.43/images/pipeshubai/pipeshub-sandbox:latest"),
        ("POST", "/v1.43/networks/x/connect"),
    ])
    def test_denied(self, policy: DockerApiPolicy, method: str, target: str) -> None:
        with pytest.raises(PolicyDenied):
            policy.route(method, target)

    @pytest.mark.parametrize("target", ["/info", "/v1.47/info"])
    def test_info_has_its_own_route(self, policy: DockerApiPolicy, target: str) -> None:
        assert policy.route("GET", target).kind == "info"

    def test_from_env_defaults_follow_sandbox_settings(self) -> None:
        p = DockerApiPolicy.from_env({"SANDBOX_DOCKER_IMAGE": "x/y:1", "SANDBOX_EGRESS_NETWORK": "n"})
        assert p.image_allowed("docker.io/x/y:1")
        assert p.allowed_networks == frozenset({"n"})
        assert DockerApiPolicy.from_env({}).image_allowed("pipeshub/sandbox:latest")


class TestContainerCreate:
    @pytest.mark.parametrize("kwargs", [_RUN, _CODING_RUN_NETWORK, _FIREWALLED_INSTALL, _RUN_WITH_QUOTA, _CODING_SANDBOX_RUN],
                             ids=["run", "coding-run-network", "firewalled-install", "run-with-quota", "coding-sandbox-run"])
    def test_admits_what_the_sandbox_sends(self, policy: DockerApiPolicy, kwargs: dict[str, Any]) -> None:
        out = policy.check_container_create(_sdk_create_body(**kwargs))
        assert out["Labels"][MANAGED_LABEL] == "true"

    def test_init_may_only_be_a_boolean(self, policy: DockerApiPolicy) -> None:
        body = _sdk_create_body(**_HARDENED_RUN)
        body["HostConfig"]["Init"] = "/host/bin/sh"
        with pytest.raises(PolicyDenied, match="Init"):
            policy.check_container_create(body)

    def test_storage_opt_may_only_set_size(self, policy: DockerApiPolicy) -> None:
        body = _sdk_create_body(**{**_HARDENED_RUN, "storage_opt": {"size": "10g", "foo": "bar"}})
        with pytest.raises(PolicyDenied, match="StorageOpt"):
            policy.check_container_create(body)

    def test_label_cannot_be_spoofed_off(self, policy: DockerApiPolicy) -> None:
        body = _sdk_create_body(**_RUN, labels={MANAGED_LABEL: "false", "keep": "1"})
        out = policy.check_container_create(body)
        assert out["Labels"] == {MANAGED_LABEL: "true", INSTANCE_LABEL: "default", "keep": "1"}

    def test_instance_label_is_stamped_over_a_client_value(self, policy: DockerApiPolicy) -> None:
        body = _sdk_create_body(**_RUN, labels={INSTANCE_LABEL: "someone-else"})
        out = policy.check_container_create(body, "stack-a")
        assert out["Labels"][INSTANCE_LABEL] == "stack-a"

    @pytest.mark.parametrize("extra", [
        {"privileged": True},
        {"volumes": {"/": {"bind": "/host", "mode": "rw"}}},
        {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]},
        {"mounts": [{"Type": "bind", "Source": "/", "Target": "/host"}]},
        {"mounts": [{"Type": "volume", "Source": "pipeshub_mongodb_data", "Target": "/d"}]},
        {"devices": ["/dev/sda:/dev/sda"]},
        {"cap_add": ["SYS_ADMIN"]},
        {"cap_add": ["CAP_SYS_PTRACE"]},
        {"pid_mode": "host"},
        {"pid_mode": "container:pipeshub-ai"},
        {"ipc_mode": "host"},
        {"uts_mode": "host"},
        {"userns_mode": "host"},
        {"security_opt": ["seccomp=unconfined"]},
        {"security_opt": ["apparmor=unconfined"]},
        {"security_opt": ["label=disable"]},
        {"cgroup_parent": "/"},
        {"sysctls": {"net.ipv4.ip_forward": "1"}},
        {"ports": {"22/tcp": 2222}},
        {"publish_all_ports": True},
        {"restart_policy": {"Name": "always"}},
        {"volumes_from": ["pipeshub-ai"]},
        {"runtime": "nvidia"},
        {"device_requests": [{"Count": -1, "Capabilities": [["gpu"]]}]},
        {"log_config": {"type": "syslog", "config": {"syslog-address": "tcp://10.0.0.5:514"}}},
        {"log_config": {"type": "gelf", "config": {"gelf-address": "udp://169.254.169.254:80"}}},
        {"log_config": {"type": "json-file", "config": {"max-file": "1000000"}}},
        {"isolation": "process"},
        {"oom_score_adj": -1000},
        {"volume_driver": "rexray"},
        {"volumes": ["/scratch"]},
    ])
    def test_refuses_host_access(self, policy: DockerApiPolicy, extra: dict[str, Any]) -> None:
        pytest.importorskip("docker")
        from docker.types import DeviceRequest, Mount

        if "mounts" in extra:
            extra = {"mounts": [Mount(m["Target"], m["Source"], type=m["Type"]) for m in extra["mounts"]]}
        if "device_requests" in extra:
            extra = {"device_requests": [DeviceRequest(count=-1, capabilities=[["gpu"]])]}
        if "log_config" in extra:
            from docker.types import LogConfig

            extra = {"log_config": LogConfig(**extra["log_config"])}
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_sdk_create_body(**{**_RUN, **extra}))

    @pytest.mark.parametrize("network_kwargs", [
        {"network_mode": "host"},
        {"network_mode": "bridge"},
        {"network_mode": "container:pipeshub-ai"},
        {"network": "pipeshub-ai_pipeshub"},
        {},  # SDK default is NetworkMode=default (the docker0 bridge)
    ])
    def test_refuses_other_networks(self, policy: DockerApiPolicy, network_kwargs: dict[str, Any]) -> None:
        base = {k: v for k, v in _RUN.items() if k not in ("network_mode", "network_disabled")}
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_sdk_create_body(**base, **network_kwargs))

    def test_refuses_other_image(self, policy: DockerApiPolicy) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_sdk_create_body(**{**_RUN, "image": "alpine"}))

    @pytest.mark.parametrize("body", [
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "MaskedPaths": []}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "CgroupnsMode": "host"}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "VolumeDriver": "local"}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": EGRESS},
         "NetworkingConfig": {"EndpointsConfig": {"pipeshub-ai_pipeshub": {}}}},
        {"Image": IMAGE, "HostConfig": "nope"},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "Cgroup": "container:pipeshub-ai"}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "LogConfig": {"Type": "fluentd"}}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "LogConfig": "syslog"}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "LogConfig": {"type": "syslog"}}},
        # Daemons up to v26 copy a top-level VolumeDriver into HostConfig.
        {"Image": IMAGE, "VolumeDriver": "rexray", "Volumes": {"/d": {}},
         "HostConfig": {"NetworkMode": "none"}},
        {"Image": IMAGE, "Binds": ["/:/host"], "Privileged": True, "HostConfig": {"NetworkMode": "none"}},
        {"Image": IMAGE, "Runtime": "nvidia", "HostConfig": {"NetworkMode": "none"}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}, "Labels": ["a=1"]},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}, "Labels": []},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}, "Labels": ""},
        [],
    ])
    def test_refuses_raw_payloads(self, policy: DockerApiPolicy, body: object) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(body)

    # Docker matches struct fields case-insensitively; the policy reads exact keys.
    @pytest.mark.parametrize("body", [
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}, "hostConfig": {"NetworkMode": "none"}},
        {"Image": IMAGE, "image": "alpine", "HostConfig": {"NetworkMode": "none"}},
        {"image": IMAGE, "HostConfig": {"NetworkMode": "none"}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "privileged": True}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "Bind\u017f": ["/:/h"]}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "Lin\u212as": ["x"]}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "RestartPolicy": {"name": "always"}}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "Mounts": [{"Type": "tmpfs", "type": "bind"}]}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none"},
         "networkingConfig": {"EndpointsConfig": {"pipeshub-ai_pipeshub": {}}}},
        {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "RestartPolicy": "always"}},
    ])
    def test_refuses_case_variant_fields(self, policy: DockerApiPolicy, body: dict[str, Any]) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(json.loads(json.dumps(body)))

    @pytest.mark.parametrize("log_config,expected", [
        ({"Type": "json-file", "Config": {}}, {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}),
        ({"Type": "local"}, {"Type": "local", "Config": {}}),
        ({"Type": "", "Config": None}, {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}),
        ({}, {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}),
        (None, {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}),
    ])
    def test_log_driver_is_pinned_and_json_file_rotates(
        self, policy: DockerApiPolicy, log_config: dict[str, Any] | None, expected: dict[str, Any],
    ) -> None:
        body = _min_body()
        if log_config is not None:
            body["HostConfig"]["LogConfig"] = log_config
        out = policy.check_container_create(body)
        # An unset driver would fall back to the daemon default, which may ship logs off-host;
        # json-file (what docker-py's containers.run reads output from) never rotates unless capped.
        assert out["HostConfig"]["LogConfig"] == expected

    def test_sdk_create_is_pinned_to_rotated_json_file_logs(self, policy: DockerApiPolicy) -> None:
        out = policy.check_container_create(_sdk_create_body(**_FIREWALLED_INSTALL))
        assert out["HostConfig"]["LogConfig"] == {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}

    def test_label_keys_are_a_map_and_keep_their_case(self, policy: DockerApiPolicy) -> None:
        body = {**_min_body(), "Labels": {"a": "1", "A": "2"}}
        assert policy.check_container_create(body)["Labels"]["A"] == "2"


def _sdk_network_body(**kwargs: object) -> dict[str, Any]:
    """The JSON body docker-py sends for ``networks.create(**kwargs)``."""
    docker = pytest.importorskip("docker")
    client = docker.DockerClient(base_url="tcp://127.0.0.1:9", version="1.43")
    captured: dict[str, Any] = {}

    def fake_post_json(url: str, data: object, **_: object) -> MagicMock:
        captured["body"] = data
        return MagicMock()

    with patch.object(client.api, "_post_json", side_effect=fake_post_json), \
         patch.object(client.api, "_result", return_value={"Id": "net"}), \
         patch("docker.models.networks.NetworkCollection.get", return_value=MagicMock()):
        client.networks.create(**kwargs)
    return json.loads(json.dumps(captured["body"]))


class TestNetworkCreate:
    def test_admits_what_the_egress_firewall_sends(self, policy: DockerApiPolicy) -> None:
        sent: dict[str, Any] = {}
        client = MagicMock()
        client.networks.list.return_value = []
        client.networks.create.side_effect = lambda **kwargs: sent.update(kwargs)
        ensure_egress_network_sync(client, EGRESS, {"pipeshub.sandbox": "egress"})
        body = _sdk_network_body(**sent)
        assert body["IPAM"]["Config"][0]["Subnet"] == "10.212.0.0/22"
        assert policy.check_network_create(body, "stack-a") == {
            **body, "EnableIPv6": False,
            "Labels": {**body["Labels"], MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-a"},
        }

    def test_the_default_pool_is_the_egress_firewalls(self) -> None:
        assert str(DockerApiPolicy.from_env({}).egress_pool) == DEFAULT_OPEN_EGRESS_POOL
        pool = DockerApiPolicy.from_env({"SANDBOX_OPEN_EGRESS_SUBNETS": "192.168.250.0/24"}).egress_pool
        assert str(pool) == "192.168.250.0/24"

    @pytest.mark.parametrize("ipam", [
        {"Driver": "default", "Config": [{"Subnet": "192.168.1.0/24"}]},
        {"Driver": "default", "Config": [{"Subnet": "10.0.0.0/8"}]},
        {"Driver": "default", "Config": [{"Subnet": "10.212.0.0/22", "Gateway": "192.168.1.1"}]},
        {"Driver": "default", "Config": [{"Subnet": "10.212.0.0/22", "IPRange": "10.212.0.0/24"}]},
        {"Driver": "default", "Config": [{"Subnet": "10.212.0.0/22", "AuxiliaryAddresses": {"a": "10.212.0.9"}}]},
        {"Driver": "default", "Config": [{"Subnet": "10.212.0.0/22", "subnet": "192.168.1.0/24"}]},
        {"Driver": "default", "Config": [{"Subnet": "10.212.0.0/22"}, {"Subnet": "10.212.4.0/22"}]},
        {"Driver": "default", "Config": [{"Subnet": "fd00::/64"}]},
        {"Driver": "default", "Config": [{"Subnet": "10.212.0.1/22"}]},
        {"Driver": "other", "Config": [{"Subnet": "10.212.0.0/22"}]},
        {"Driver": "default", "Options": {"x": "y"}, "Config": [{"Subnet": "10.212.0.0/22"}]},
        {"Driver": "default", "Config": [{"Subnet": "10.212.0.0/22"}], "Extra": 1},
    ])
    def test_refuses_addresses_outside_the_pool_or_anything_else(
        self, policy: DockerApiPolicy, ipam: dict[str, Any],
    ) -> None:
        body = {"Name": EGRESS, "Driver": "bridge", "IPAM": ipam}
        with pytest.raises(PolicyDenied):
            policy.check_network_create(body)

    def test_refuses_ipv6(self, policy: DockerApiPolicy) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_network_create(_sdk_network_body(name=EGRESS, driver="bridge", enable_ipv6=True))

    def test_admits_egress_network(self, policy: DockerApiPolicy) -> None:
        body = {"Name": EGRESS, "Driver": "bridge", "Options": {"com.docker.network.bridge.enable_icc": "false"},
                "IPAM": None, "CheckDuplicate": True, "Labels": {"pipeshub.sandbox": "egress"}}
        assert policy.check_network_create(body)["Labels"] == {
            "pipeshub.sandbox": "egress", MANAGED_LABEL: "true", INSTANCE_LABEL: "default",
        }

    @pytest.mark.parametrize("body", [
        {"Name": "pipeshub-ai_pipeshub", "Driver": "bridge"},
        {"Name": EGRESS, "Driver": "macvlan"},
        {"Name": EGRESS, "Driver": "bridge", "Options": {"com.docker.network.bridge.name": "docker0"}},
        {"Name": EGRESS, "Driver": "bridge", "ConfigFrom": {"Network": "x"}},
    ])
    def test_refuses_others(self, policy: DockerApiPolicy, body: dict[str, Any]) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_network_create(body)

    @pytest.mark.parametrize("body", [
        {"Name": EGRESS, "driver": "macvlan"},
        {"Name": EGRESS, "Driver": "bridge", "driver": "macvlan"},
        {"name": "pipeshub-ai_pipeshub"},
        {"Name": EGRESS, "Driver": "bridge", "options": {"com.docker.network.bridge.name": "docker0"}},
    ])
    def test_refuses_case_variant_fields(self, policy: DockerApiPolicy, body: dict[str, Any]) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_network_create(body)


class _FakeDaemon:
    """Minimal HTTP/1.1 server on a unix socket that records each request."""

    def __init__(self, containers: dict[str, dict[str, Any]],
                 networks: dict[str, dict[str, Any]] | None = None) -> None:
        self.containers = containers
        self.networks = networks or {}
        self.requests: list[tuple[str, str, bytes]] = []
        self.before_inspect: Callable[[], Awaitable[None]] | None = None
        self.info: tuple[str, bytes] = ("200 OK", json.dumps({
            "NCPU": 4, "MemTotal": 16 * 2**30, "KernelVersion": "6.8.0", "Driver": "overlay2",
            "HttpProxy": "http://user:secret@proxy:3128", "Labels": ["x=y"],
            "RegistryConfig": {"Mirrors": ["m"]}, "Containers": 99,
        }).encode())

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        method, target, _ = head.split("\r\n")[0].split(" ")
        length = 0
        for line in head.split("\r\n")[1:]:
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1])
        body = await reader.readexactly(length) if length else b""
        self.requests.append((method, target, body))

        path = target.split("?", 1)[0]
        if path == "/info":
            status, payload = self.info
            writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        elif path.endswith("/json") and "/containers/" in path:
            if self.before_inspect is not None:
                await self.before_inspect()
            ident = path.split("/containers/")[1].split("/")[0]
            info = self.containers.get(ident)
            if info is None:
                payload, status = b'{"message":"No such container"}', "404 Not Found"
            else:
                payload, status = json.dumps(info).encode(), "200 OK"
            writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        elif method == "GET" and path.split("/")[-2:-1] == ["networks"]:
            info = self.networks.get(path.rsplit("/", 1)[1])
            payload = json.dumps(info).encode() if info else b'{"message":"not found"}'
            status = "200 OK" if info else "404 Not Found"
            writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        elif path.endswith("/logs"):
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: keep-alive\r\n\r\n"
                         b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
        else:
            payload = b'{"ok":true}'
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        await writer.drain()
        writer.close()


def with_token(raw: bytes, token: str | None = TOKEN) -> bytes:
    """``raw`` with the caller's credential after its request line (``token=None``: none)."""
    if token is not None and b"\r\n" in raw:
        line, rest = raw.split(b"\r\n", 1)
        raw = line + f"\r\nAuthorization: Bearer {token}".encode() + b"\r\n" + rest
    return raw


async def _request(port: int, raw: bytes, *, token: str | None = TOKEN) -> bytes:
    raw = with_token(raw, token)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data


@pytest.fixture
async def proxied(tmp_path, policy: DockerApiPolicy) -> AsyncIterator[tuple[_FakeDaemon, int]]:
    owned = {"Id": "f" * 64, "Config": {"Labels": {MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-a"}}}
    foreign = {"Id": "e" * 64, "Config": {"Labels": {}}}
    other_stack = {"Id": "d" * 64, "Config": {"Labels": {MANAGED_LABEL: "true", INSTANCE_LABEL: "stack-b"}}}
    older_proxy = {"Id": "c" * 64, "Config": {"Labels": {MANAGED_LABEL: "true"}}}
    daemon = _FakeDaemon(
        {"owned": owned, "f" * 64: owned, "foreign": foreign,
         "other-stack": other_stack, "older-proxy": older_proxy},
        networks={
            "egressid": {"Id": "egressid", "Name": EGRESS, "Labels": {INSTANCE_LABEL: "stack-a"}},
            "appnetid": {"Id": "appnetid", "Name": "pipeshub-ai_pipeshub"},
        },
    )
    # Under /tmp, not pytest's tmp_path: a long TMPDIR pushes the socket past AF_UNIX's 108-byte path limit.
    sock_dir = tempfile.mkdtemp(prefix="dproxy-", dir="/tmp")
    sock = f"{sock_dir}/d.sock"
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    proxy = DockerSocketProxy(policy, sock, caller_token=TOKEN, instance="stack-a")
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield daemon, port
    finally:
        server.close()
        upstream.close()
        shutil.rmtree(sock_dir, ignore_errors=True)


class TestRequestHead:
    def test_well_formed_head_is_parsed(self) -> None:
        req = _parse_request_head(
            b"POST /v1.43/containers/create HTTP/1.1\r\nHost: x\r\n"
            b"Content-Length: 2\r\n\r\n"
        )
        assert (req.method, req.target, req.content_length) == ("POST", "/v1.43/containers/create", 2)

    @pytest.mark.parametrize("raw", [
        # A bare LF ends a header line for the daemon but not for the proxy.
        b"GET /_ping HTTP/1.1\r\nX: a\nTransfer-Encoding: chunked\r\n\r\n",
        b"GET /_ping HTTP/1.1\r\nX: a\nConnection: keep-alive\r\n\r\n",
        b"GET /_ping HTTP/1.1\r\nX: a\x00b\r\n\r\n",
        b"GET /_ping\n HTTP/1.1\r\n\r\n",
        b"GE\nT /_ping HTTP/1.1\r\n\r\n",
        b"GET /_ping HTTP/1.1\r\nX : a\r\n\r\n",
        b"GET /_ping HTTP/1.1\r\nX: a\r\n folded\r\n\r\n",
        b"GET /_ping HTTP/2.0\r\n\r\n",
        b"PUT /containers/x/archive HTTP/1.0\r\nTransfer-Encoding: chunked\r\n\r\n",
    ])
    def test_injected_or_malformed_lines_are_refused(self, raw: bytes) -> None:
        with pytest.raises(PolicyDenied):
            _parse_request_head(raw)

    @pytest.mark.parametrize("framing", [
        b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n",
        b"Content-Length: 5\r\nContent-Length: 5\r\n",
        b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n",
        b"Transfer-Encoding: gzip, chunked\r\n",
    ])
    def test_ambiguous_framing_is_refused(self, framing: bytes) -> None:
        with pytest.raises(PolicyDenied):
            _parse_request_head(b"POST /containers/create HTTP/1.1\r\n" + framing + b"\r\n")


class TestProxyServer:
    async def test_ping_is_forwarded_with_connection_close(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"GET /_ping HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 200")
        assert b"Connection: close" in resp
        assert daemon.requests[0][:2] == ("GET", "/_ping")

    async def test_create_is_rewritten_with_label(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps(_min_body()).encode()
        resp = await _request(port, b"POST /v1.43/containers/create HTTP/1.1\r\nHost: x\r\n"
                              b"Content-Type: application/json\r\nContent-Length: "
                              + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert resp.startswith(b"HTTP/1.1 200")
        sent = json.loads(daemon.requests[-1][2])
        assert sent["Labels"][MANAGED_LABEL] == "true"
        assert sent["Labels"][INSTANCE_LABEL] == "stack-a"

    async def test_another_stacks_container_is_refused_by_name(self, proxied) -> None:
        _daemon, port = proxied
        resp = await _request(port, b"GET /containers/other-stack/json HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 403")
        assert b"not managed by this sandbox proxy" in resp

    async def test_chunked_create_body_is_read(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps(_min_body()).encode()
        chunked = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        resp = await _request(port, b"POST /containers/create HTTP/1.1\r\nHost: x\r\n"
                              b"Transfer-Encoding: chunked\r\n\r\n" + chunked)
        assert resp.startswith(b"HTTP/1.1 200")
        assert MANAGED_LABEL in daemon.requests[-1][2].decode()

    async def test_privileged_create_never_reaches_daemon(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps({"Image": IMAGE, "HostConfig": {"Privileged": True, "NetworkMode": "none"}}).encode()
        resp = await _request(port, b"POST /containers/create HTTP/1.1\r\nHost: x\r\nContent-Length: "
                              + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert resp.startswith(b"HTTP/1.1 403")
        assert b"privileged" in resp
        assert daemon.requests == []

    async def test_owned_container_is_addressed_by_full_id(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"POST /v1.43/containers/owned/start HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1][:2] == ("POST", f"/v1.43/containers/{'f' * 64}/start")

    @pytest.mark.parametrize("name", ["foreign", "missing", "other-stack", "older-proxy"])
    async def test_unowned_container_is_refused(self, proxied, name: str) -> None:
        daemon, port = proxied
        resp = await _request(port, f"PUT /containers/{name}/archive?path=/ HTTP/1.1\r\nHost: x\r\n"
                                    "Content-Length: 4\r\n\r\nabcd".encode())
        assert resp.startswith(b"HTTP/1.1 403")
        assert [r[0] for r in daemon.requests] == ["GET"]  # only the ownership inspect

    async def test_archive_upload_body_is_streamed(self, proxied) -> None:
        daemon, port = proxied
        payload = b"x" * 200_000
        resp = await _request(port, b"PUT /containers/owned/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
                              b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1][2] == payload

    async def test_paused_upload_survives_gc_during_ownership_check(self, proxied) -> None:
        daemon, port = proxied

        async def collect() -> None:
            # Let the body fill the proxy's buffer so it pauses reading the client.
            await asyncio.sleep(0.1)
            gc.collect()

        daemon.before_inspect = collect
        payload = b"x" * 200_000
        resp = await _request(port, b"PUT /containers/owned/archive?path=/src HTTP/1.1\r\nHost: x\r\n"
                              b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1][2] == payload

    async def test_chunked_response_is_passed_through(self, proxied) -> None:
        _, port = proxied
        resp = await _request(port, b"GET /containers/owned/logs?stdout=1 HTTP/1.1\r\nHost: x\r\n\r\n")
        head, _, body = resp.partition(b"\r\n\r\n")
        assert b"Connection: close" in head and b"keep-alive" not in head
        assert body == b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"

    async def test_info_passes_only_the_sizing_fields(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"GET /v1.47/info HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 200")
        body = json.loads(resp.split(b"\r\n\r\n", 1)[1])
        assert body == {
            "NCPU": 4, "MemTotal": 16 * 2**30, "KernelVersion": "6.8.0", "Driver": "overlay2", "DriverStatus": [],
        }
        assert daemon.requests[-1][:2] == ("GET", "/info")

    @pytest.mark.parametrize("upstream", [
        ("500 Internal Server Error", b'{"message":"boom"}'),
        ("200 OK", b"not json"),
        ("200 OK", b"[1, 2]"),
    ])
    async def test_a_bad_upstream_info_is_a_502(self, proxied, upstream: tuple[str, bytes]) -> None:
        daemon, port = proxied
        daemon.info = upstream
        resp = await _request(port, b"GET /info HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 502")

    async def test_unlisted_endpoint_is_refused(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"POST /containers/foreign/exec HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []

    async def test_malformed_request_is_refused(self, proxied) -> None:
        _, port = proxied
        resp = await _request(port, b"garbage\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 403")

    async def test_expect_continue_on_create_does_not_stall(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps(_min_body()).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"POST /containers/create HTTP/1.1\r\nHost: x\r\nExpect: 100-continue\r\n"
                     + f"Authorization: Bearer {TOKEN}\r\n".encode()
                     + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n")
        await writer.drain()
        interim = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        assert interim.startswith(b"HTTP/1.1 100")
        writer.write(body)
        await writer.drain()
        final = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        assert final.startswith(b"HTTP/1.1 200")
        assert MANAGED_LABEL in daemon.requests[-1][2].decode()

    @pytest.mark.parametrize("raw_body", [
        b'{"Image": "%s", "HostConfig": {"NetworkMode": "none"}, "HostConfig": {"NetworkMode": "none"}}',
        b'{"Image": "%s", "HostConfig": {"NetworkMode": "none"}, "hostconfig": {"NetworkMode": "none"}}',
    ])
    async def test_duplicate_or_case_variant_keys_never_reach_daemon(self, proxied, raw_body: bytes) -> None:
        daemon, port = proxied
        body = raw_body.replace(b"%s", IMAGE.encode())
        resp = await _request(port, b"POST /containers/create HTTP/1.1\r\nHost: x\r\nContent-Length: "
                              + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []

    async def test_sandbox_network_can_be_removed(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"DELETE /v1.43/networks/egressid HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 200")
        assert daemon.requests[-1][:2] == ("DELETE", "/v1.43/networks/egressid")

    @pytest.mark.parametrize("ident", ["appnetid", "missing"])
    async def test_other_networks_cannot_be_removed(self, proxied, ident: str) -> None:
        daemon, port = proxied
        resp = await _request(port, f"DELETE /networks/{ident} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert resp.startswith(b"HTTP/1.1 403")
        assert [r[0] for r in daemon.requests] == ["GET"]

    async def test_absolute_form_target_is_refused(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"GET http://docker/_ping HTTP/1.1\r\nHost: x\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 403")
        assert daemon.requests == []



class TestDropRoot:
    """R51-COMPAT (ADR-OPS-07): Compose without DOCKER_GID starts the proxy as root; it serves as uid 1000 with the
    Docker socket's group, so an .env from a release without DOCKER_GID keeps code execution."""

    class _Ops:
        def __init__(self, uid: int, gid: int) -> None:
            self.uid, self.gid, self.calls = uid, gid, []

        def getuid(self) -> int:
            return self.uid

        def stat(self, path: str) -> object:
            self.calls.append(("stat", path))
            return type("St", (), {"st_gid": self.gid})()

        def setgroups(self, groups: list[int]) -> None:
            self.calls.append(("setgroups", groups))

        def setgid(self, gid: int) -> None:
            self.calls.append(("setgid", gid))

        def setuid(self, uid: int) -> None:
            self.calls.append(("setuid", uid))

    def test_root_takes_the_socket_group_and_becomes_uid_1000(self) -> None:
        from app.sandbox.docker_proxy import drop_root

        ops = self._Ops(0, 998)
        assert drop_root("/var/run/docker.sock", ops) == 998
        assert ops.calls == [("stat", "/var/run/docker.sock"), ("setgroups", [998]), ("setgid", 1000),
                             ("setuid", 1000)]

    def test_an_unprivileged_start_changes_nothing(self) -> None:
        from app.sandbox.docker_proxy import drop_root

        ops = self._Ops(1000, 998)
        assert drop_root("/var/run/docker.sock", ops) is None
        assert ops.calls == []


class TestResourceLimits:
    @pytest.mark.parametrize("kwargs", [_RUN, _CODING_RUN_NETWORK, _FIREWALLED_INSTALL, _HARDENED_RUN],
                             ids=["run", "coding-run-network", "firewalled-install", "hardened-run"])
    def test_real_creates_get_no_swap(self, policy: DockerApiPolicy, kwargs: dict[str, Any]) -> None:
        out = policy.check_container_create(_sdk_create_body(**kwargs))
        assert out["HostConfig"]["MemorySwap"] == out["HostConfig"]["Memory"]

    @pytest.mark.parametrize(("drop", "message"), [
        ("mem_limit", "HostConfig.Memory is required"),
        ("nano_cpus", "HostConfig.NanoCpus is required"),
        ("pids_limit", "HostConfig.PidsLimit is required"),
    ])
    def test_each_limit_is_required(self, policy: DockerApiPolicy, drop: str, message: str) -> None:
        kwargs = {k: v for k, v in _RUN.items() if k != drop}
        with pytest.raises(PolicyDenied, match=message):
            policy.check_container_create(_sdk_create_body(**kwargs))

    @pytest.mark.parametrize(("override", "message"), [
        ({"mem_limit": 1024 * 1024 * 1024 + 1}, "exceeds the maximum of 1024 MB"),
        ({"nano_cpus": 1_500_000_000}, "exceeds the maximum of 1 CPUs"),
        ({"pids_limit": 0}, "PidsLimit is required"),
        ({"pids_limit": -1}, "PidsLimit is required"),
        ({"pids_limit": 257}, "PidsLimit 257 exceeds the maximum of 256"),
        ({"memswap_limit": -1}, "MemorySwap must equal Memory"),
        ({"memswap_limit": 2 * 512 * 1024 * 1024}, "MemorySwap must equal Memory"),
        ({"oom_score_adj": -1}, "OomScoreAdj may not be negative"),
        ({"oom_score_adj": -1000}, "OomScoreAdj may not be negative"),
        ({"oom_score_adj": 1001}, "OomScoreAdj may not be above 1000"),
    ])
    def test_limits_are_enforced(self, policy: DockerApiPolicy, override: dict[str, Any], message: str) -> None:
        with pytest.raises(PolicyDenied, match=message):
            policy.check_container_create(_sdk_create_body(**{**_RUN, **override}))

    @pytest.mark.parametrize("value", [True, 5.12e8])
    def test_memory_must_be_a_json_integer(self, policy: DockerApiPolicy, value: object) -> None:
        body = _min_body(Memory=value)
        with pytest.raises(PolicyDenied, match="HostConfig.Memory is required"):
            policy.check_container_create(body)

    def test_swap_equal_to_memory_is_kept(self, policy: DockerApiPolicy) -> None:
        out = policy.check_container_create(_sdk_create_body(**{**_RUN, "memswap_limit": 512 * 1024 * 1024}))
        assert out["HostConfig"]["MemorySwap"] == 512 * 1024 * 1024

    @pytest.mark.parametrize("value", [0, 1000])
    def test_oom_score_adj_in_range_is_admitted(self, policy: DockerApiPolicy, value: int) -> None:
        # Raw body: docker-py drops a falsy oom_score_adj, so 0 never reaches the proxy through it.
        body = _min_body(OomScoreAdj=value)
        assert policy.check_container_create(body)["HostConfig"]["OomScoreAdj"] == value

    def test_the_proxy_never_adds_oom_score_adj(self, policy: DockerApiPolicy) -> None:
        out = policy.check_container_create(_sdk_create_body(**_RUN))
        assert "OomScoreAdj" not in out["HostConfig"]

    def test_case_variant_memory_swap_is_refused(self, policy: DockerApiPolicy) -> None:
        body = _min_body(memoryswap=-1)
        del body["HostConfig"]["MemorySwap"]
        with pytest.raises(PolicyDenied, match="must be spelled 'MemorySwap'"):
            policy.check_container_create(body)

    def test_from_env_defaults_and_overrides(self) -> None:
        default = DockerApiPolicy.from_env({})
        assert (default.max_memory_bytes, default.max_nano_cpus, default.max_pids) == (1024 * 1024 * 1024, 10**9, 256)
        custom = DockerApiPolicy.from_env({
            "DOCKER_PROXY_MAX_MEMORY_MB": "256", "DOCKER_PROXY_MAX_CPUS": "0.5", "DOCKER_PROXY_MAX_PIDS": "64",
        })
        assert (custom.max_memory_bytes, custom.max_nano_cpus, custom.max_pids) == (256 * 1024 * 1024, 5 * 10**8, 64)
        assert custom.limits_text() == "memory<= 256 MB (no swap), cpus<= 0.5, pids<= 64, oom_score_adj 0..1000"

    @pytest.mark.parametrize("raw", ["abc", "0", "-5"])
    def test_invalid_env_warns_and_uses_the_default(self, raw: str, caplog) -> None:
        import logging

        with caplog.at_level(logging.WARNING):
            policy = DockerApiPolicy.from_env({"DOCKER_PROXY_MAX_MEMORY_MB": raw})
        assert policy.max_memory_bytes == 1024 * 1024 * 1024
        assert "DOCKER_PROXY_MAX_MEMORY_MB" in caplog.text

    async def test_forwarded_body_has_swap_equal_to_memory(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps(_min_body()).encode()
        resp = await _request(port, b"POST /containers/create HTTP/1.1\r\nHost: x\r\nContent-Length: "
                              + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert resp.startswith(b"HTTP/1.1 200")
        sent = json.loads(daemon.requests[-1][2])
        assert sent["HostConfig"]["MemorySwap"] == _FLOORED_HOST["Memory"]


class TestLocalRoutes:
    @pytest.mark.parametrize("target", ["/_pipeshub/host", "/v1.43/_pipeshub/host"])
    def test_host_route_is_local(self, policy: DockerApiPolicy, target: str) -> None:
        assert policy.route("GET", target).kind == "local_host_id"

    @pytest.mark.parametrize(("method", "target"), [
        ("POST", "/_pipeshub/host"), ("DELETE", "/_pipeshub/host"),
        ("GET", "/_pipeshub/host?x=1"), ("GET", "/_pipeshub/other"),
    ])
    def test_other_local_requests_are_refused(self, policy: DockerApiPolicy, method: str, target: str) -> None:
        with pytest.raises(PolicyDenied):
            policy.route(method, target)

    async def test_host_route_never_opens_the_upstream(self, tmp_path, policy: DockerApiPolicy) -> None:
        boot = tmp_path / "boot_id"
        boot.write_text("abc\n")
        proxy = DockerSocketProxy(policy, str(tmp_path / "missing.sock"), caller_token=TOKEN)
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with patch("app.sandbox.docker_proxy._BOOT_ID_PATH", str(boot)):
                resp = await _request(port, b"GET /v1.43/_pipeshub/host HTTP/1.1\r\nHost: x\r\n\r\n")
            assert resp.startswith(b"HTTP/1.1 200")
            body = json.loads(resp.split(b"\r\n\r\n", 1)[1])
            assert body["v"] == 1 and len(body["bootIdHash"]) == 32
            int(body["bootIdHash"], 16)
            resp = await _request(port, b"POST /_pipeshub/host HTTP/1.1\r\nHost: x\r\n\r\n")
            assert resp.startswith(b"HTTP/1.1 403")
        finally:
            server.close()


SIDECAR_EXPORT = """v 1
seq {seq}
memory.max {mem_max}
memory.current 1000000000
cpu.max 200000 100000
memory.stat.anon 600000000
memory.stat.file 300000000
memory.stat.shmem 100000000
memory.events.local.oom {oom}
memory.events.local.max 3
cpu.stat.usage_usec {usage}
cpu.stat.nr_periods {periods}
cpu.stat.nr_throttled {throttled}
memory.pressure.full.avg10 1.50
cpu.pressure.some.avg10 20.00
dockerd.rss_kb 96000
containerd.rss_kb 40000
"""


def _export(path, *, seq: int = 1, usage: int = 0, periods: int = 0, throttled: int = 0,
            oom: int = 0, mem_max: str = "4294967296", mtime: float | None = None) -> None:
    import os

    path.write_text(SIDECAR_EXPORT.format(
        seq=seq, usage=usage, periods=periods, throttled=throttled, oom=oom, mem_max=mem_max,
    ))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class TestSidecarStats:
    def test_realistic_export(self, tmp_path) -> None:
        import time

        from app.sandbox.docker_proxy import SidecarStats

        path = tmp_path / "stats"
        _export(path)
        block, error = SidecarStats(str(path)).read()
        assert error is None and block is not None
        assert block["memLimitBytes"] == 4294967296
        # current - (file - shmem): tmpfs stays in the working set.
        assert block["memWorkingSetBytes"] == 1000000000 - 200000000
        assert block["memPsiFullAvg10"] == pytest.approx(0.015)
        assert block["cpuPsiSomeAvg10"] == pytest.approx(0.2)
        assert block["cpuLimitMillis"] == 2000
        assert (block["dockerdRssBytes"], block["containerdRssBytes"]) == (96000 * 1024, 40000 * 1024)
        assert block["cpuUtil"] is None and block["cpuThrottledRatio"] is None and block["oomEventsDelta"] is None
        assert block["ageS"] < 5 and time.time() > 0

    def test_rates_between_snapshots(self, tmp_path) -> None:
        import time

        from app.sandbox.docker_proxy import SidecarStats

        path, now = tmp_path / "stats", time.time()
        stats = SidecarStats(str(path), clock=lambda: now)
        _export(path, seq=1, usage=0, periods=0, throttled=0, oom=0, mtime=now - 15)
        stats.read()
        _export(path, seq=4, usage=15_000_000, periods=150, throttled=50, oom=1, mtime=now)
        block, _ = stats.read()
        assert block is not None
        assert block["windowS"] == pytest.approx(15.0)
        assert block["cpuUtil"] == pytest.approx(0.5)
        assert block["cpuThrottledRatio"] == pytest.approx(50 / 150)
        assert block["oomEventsDelta"] == 1

    def test_a_counter_going_back_resets_the_rates(self, tmp_path) -> None:
        import time

        from app.sandbox.docker_proxy import SidecarStats

        path, now = tmp_path / "stats", time.time()
        stats = SidecarStats(str(path), clock=lambda: now)
        _export(path, seq=9, usage=50_000_000, mtime=now - 15)
        stats.read()
        _export(path, seq=1, usage=1_000, mtime=now)
        block, _ = stats.read()
        assert block is not None and block["cpuUtil"] is None and block["windowS"] is None

    def test_unlimited_memory_is_null(self, tmp_path) -> None:
        from app.sandbox.docker_proxy import SidecarStats

        _export(tmp_path / "stats", mem_max="max")
        block, _ = SidecarStats(str(tmp_path / "stats")).read()
        assert block is not None and block["memLimitBytes"] is None

    def test_errors(self, tmp_path) -> None:
        import time

        from app.sandbox.docker_proxy import SidecarStats

        assert SidecarStats(None).read() == (None, "not_configured")
        assert SidecarStats(str(tmp_path / "nope")).read() == (None, "missing")
        old = tmp_path / "old"
        _export(old, mtime=time.time() - 21)
        assert SidecarStats(str(old)).read() == (None, "stale")
        big = tmp_path / "big"
        big.write_text("x" * (16 * 1024 + 1))
        assert SidecarStats(str(big)).read() == (None, "unparsable")
        link = tmp_path / "link"
        _export(tmp_path / "target")
        link.symlink_to(tmp_path / "target")
        assert SidecarStats(str(link)).read() == (None, "unparsable")
        bad = tmp_path / "bad"
        bad.write_text("memory.current lots\n")
        assert SidecarStats(str(bad)).read() == (None, "unparsable")
        junk = tmp_path / "junk"
        junk.write_text("hello world\n\n# comment\nmemory.current 5\n")
        block, error = SidecarStats(str(junk)).read()
        assert error is None and block is not None and block["memWorkingSetBytes"] == 5


class _UsageDaemon:
    """A daemon that lists containers and answers one-shot stats."""

    def __init__(self, ids: list[str], *, slow: set[str] | None = None) -> None:
        self.ids = ids
        self.slow = slow or set()
        self.usage: dict[str, int] = dict.fromkeys(ids, 0)
        self.requests: list[str] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        target = head.split("\r\n")[0].split(" ")[1]
        self.requests.append(target)
        path = target.split("?", 1)[0]
        if path == "/containers/json":
            payload = json.dumps([{"Id": i, "Names": ["/x"], "Labels": {"a": "b"}} for i in self.ids]).encode()
        else:
            ident = path.split("/")[2]
            if ident in self.slow:
                await asyncio.sleep(3)
            payload = json.dumps({
                "memory_stats": {"usage": 150, "stats": {"file": 60, "shmem": 10}},
                "cpu_stats": {"cpu_usage": {"total_usage": self.usage[ident]}},
            }).encode()
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        await writer.drain()
        writer.close()


@pytest.fixture
async def usage_proxy(tmp_path, policy: DockerApiPolicy) -> AsyncIterator[tuple[_UsageDaemon, DockerSocketProxy, int]]:
    daemon = _UsageDaemon(["a" * 64, "b" * 64])
    sock = str(tmp_path / "u.sock")
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    proxy = DockerSocketProxy(policy, sock, caller_token=TOKEN, instance="stack-a")
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    yield daemon, proxy, server.sockets[0].getsockname()[1]
    server.close()
    upstream.close()


async def _usage(port: int, query: str = "") -> tuple[bytes, dict[str, Any] | None]:
    resp = await _request(port, f"GET /v1.43/_pipeshub/usage{query} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    body = resp.split(b"\r\n\r\n", 1)[1]
    return resp, json.loads(body) if resp.startswith(b"HTTP/1.1 200") else None


class TestUsageRoute:
    @pytest.mark.parametrize(("method", "target"), [
        ("POST", "/_pipeshub/usage"), ("GET", "/_pipeshub/usage?foo=1"),
        ("GET", "/_pipeshub/usage?scope=all"), ("GET", "/_pipeshub/usage?scope=containers&scope=containers"),
        ("GET", "/containers/abc/stats"),
    ])
    def test_refused(self, policy: DockerApiPolicy, method: str, target: str) -> None:
        with pytest.raises(PolicyDenied):
            policy.route(method, target)

    def test_scopes(self, policy: DockerApiPolicy) -> None:
        assert policy.route("GET", "/v1.43/_pipeshub/usage") == Route("local_usage")
        assert policy.route("GET", "/_pipeshub/usage?scope=containers") == Route("local_usage", scope="containers")

    async def test_sidecar_scope_never_opens_the_upstream(self, tmp_path, policy: DockerApiPolicy) -> None:
        _export(tmp_path / "stats")
        proxy = DockerSocketProxy(
            policy, str(tmp_path / "missing.sock"), caller_token=TOKEN, sidecar_stats_file=str(tmp_path / "stats"),
        )
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        try:
            _, body = await _usage(server.sockets[0].getsockname()[1])
        finally:
            server.close()
        assert body is not None and body["v"] == 1 and body["sidecarError"] is None
        assert body["sidecar"]["memLimitBytes"] == 4294967296

    async def test_without_a_file_it_is_not_configured(self, usage_proxy) -> None:
        _, _, port = usage_proxy
        _, body = await _usage(port)
        assert body == {"v": 1, "sidecar": None, "sidecarError": "not_configured"}

    async def test_containers_are_listed_by_both_labels_and_summed(self, usage_proxy) -> None:
        daemon, _, port = usage_proxy
        _, body = await _usage(port, "?scope=containers")
        assert body is not None and body["containersError"] is None
        block = body["containers"]
        assert block == {"running": 2, "memWorkingSetBytes": 200, "cpuCores": None, "partial": False, "truncated": False}
        listed = urllib.parse.unquote(daemon.requests[0])
        assert '"ai.pipeshub.docker-proxy.managed=true"' in listed
        assert '"ai.pipeshub.docker-proxy.instance=stack-a"' in listed and '"running"' in listed
        assert all("one-shot=true" in r and "stream=false" in r for r in daemon.requests[1:])

    async def test_cpu_counts_only_containers_seen_twice_and_is_cached(self, usage_proxy) -> None:
        daemon, proxy, port = usage_proxy
        await _usage(port, "?scope=containers")
        calls = len(daemon.requests)
        await _usage(port, "?scope=containers")
        assert len(daemon.requests) == calls
        proxy._containers_cache = None
        for ident in daemon.ids:
            proxy._containers_cpu[ident] = (0.0, proxy._containers_cpu[ident][1] - 2.0)
        daemon.usage = dict.fromkeys(daemon.ids, 2_000_000_000)
        _, body = await _usage(port, "?scope=containers")
        assert body is not None and body["containers"]["cpuCores"] == pytest.approx(2.0, rel=0.05)

    async def test_slow_stats_give_partial_within_the_budget(self, tmp_path, policy: DockerApiPolicy, monkeypatch) -> None:
        import time

        from app.sandbox import docker_proxy as proxy_mod

        # The slow container answers after 3 s; with the call and overall budgets cut to 0.1 s and 0.2 s, a call that
        # waited for it is told apart from one that kept to the budget by seconds, not by the 0.5 s the defaults leave.
        monkeypatch.setattr(proxy_mod, "_STATS_CALL_TIMEOUT_S", 0.1)
        monkeypatch.setattr(proxy_mod, "_STATS_BUDGET_S", 0.2)
        daemon = _UsageDaemon(["a" * 64, "b" * 64], slow={"b" * 64})
        sock = str(tmp_path / "s.sock")
        upstream = await asyncio.start_unix_server(daemon.handle, sock)
        proxy = DockerSocketProxy(policy, sock, caller_token=TOKEN)
        try:
            started = time.monotonic()
            block, _ = await proxy.containers_usage()
            # wall-clock-ok: the budget is 0.2 s and the slow answer comes at 3 s; 1.5 s is seven times the one, half the other
            assert time.monotonic() - started < 1.5
        finally:
            upstream.close()
        assert block is not None and block["partial"] is True and block["memWorkingSetBytes"] == 100

    async def test_many_containers_are_capped(self, tmp_path, policy: DockerApiPolicy) -> None:
        daemon = _UsageDaemon([f"{i:064x}" for i in range(300)])
        sock = str(tmp_path / "m.sock")
        upstream = await asyncio.start_unix_server(daemon.handle, sock)
        proxy = DockerSocketProxy(policy, sock, caller_token=TOKEN)
        try:
            block, _ = await proxy.containers_usage()
        finally:
            upstream.close()
        assert block is not None and block["running"] == 256 and block["truncated"] is True
        assert set(block) == {"running", "memWorkingSetBytes", "cpuCores", "partial", "truncated"}

"""Tests for the sandbox Docker API proxy (app/sandbox/docker_proxy.py)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    CONTAINER_HARDENING,
    firewalled_container_kwargs,
)
from app.sandbox.docker_proxy import (
    MANAGED_LABEL,
    DockerApiPolicy,
    DockerSocketProxy,
    PolicyDenied,
    normalize_image,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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

    with patch.object(client.api, "_post_json", side_effect=fake_post_json), \
         patch.object(client.api, "_result", return_value={"Id": "abc"}), \
         patch("docker.models.containers.ContainerCollection.get", return_value=MagicMock()):
        client.containers.create(**kwargs)
    return json.loads(json.dumps(captured["body"]))


# The kwargs each sandbox code path passes to containers.create today.
_LEGACY_RUN: dict[str, Any] = {
    "image": IMAGE, "command": ["python3", "/src/main.py"], "environment": {"OUTPUT_DIR": "/output"},
    "working_dir": "/src", "mem_limit": 512 * 1024 * 1024, "nano_cpus": 500_000_000,
    "network_mode": "none", "network_disabled": True, "read_only": False,
    "tmpfs": {"/tmp": "size=100M"}, "detach": True,
}
_INSTALL: dict[str, Any] = {
    "image": IMAGE, "command": ["sh", "-c", "pip install x"], "environment": {},
    "mem_limit": 512 * 1024 * 1024, "nano_cpus": 500_000_000,
    "network": EGRESS, "network_disabled": False, "detach": True,
}
_CODING_RUN_NETWORK: dict[str, Any] = {
    "image": IMAGE, "command": ["python3", "main.py"], "environment": {"OUTPUT_DIR": "/output"},
    "working_dir": "/src", "mem_limit": 1, "nano_cpus": 1, "tmpfs": {"/tmp": "size=100M"},
    "detach": True, "network": EGRESS, "network_disabled": False,
}
# Built with the egress firewall's own helpers, so a change there that the
# proxy would refuse fails here rather than in production.
_FIREWALLED_INSTALL: dict[str, Any] = {
    **_INSTALL,
    **firewalled_container_kwargs(
        ["sh", "-c", "pip install x"], network_cidrs=["172.30.0.0/16"],
        allowed_cidrs=["10.20.0.0/16"], token="0" * 32,
    ),
}
_HARDENED_RUN: dict[str, Any] = {**_LEGACY_RUN, **CONTAINER_HARDENING}


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
        ("GET", "/v1.43/networks?filters=%7B%7D"),
        ("POST", "/v1.43/images/create?fromImage=pipeshubai%2Fpipeshub-sandbox&tag=latest"),
        ("GET", "/v1.43/images/pipeshubai/pipeshub-sandbox:latest/json"),
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
        ("GET", "/v1.43/info"),
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

    def test_from_env_defaults_follow_sandbox_settings(self) -> None:
        p = DockerApiPolicy.from_env({"SANDBOX_DOCKER_IMAGE": "x/y:1", "SANDBOX_EGRESS_NETWORK": "n"})
        assert p.image_allowed("docker.io/x/y:1")
        assert p.allowed_networks == frozenset({"n"})
        assert DockerApiPolicy.from_env({}).image_allowed("pipeshub/sandbox:latest")


class TestContainerCreate:
    @pytest.mark.parametrize("kwargs", [_LEGACY_RUN, _INSTALL, _CODING_RUN_NETWORK, _FIREWALLED_INSTALL, _HARDENED_RUN],
                             ids=["legacy-run", "install", "coding-run-network", "firewalled-install", "hardened-run"])
    def test_admits_what_the_sandbox_sends(self, policy: DockerApiPolicy, kwargs: dict[str, Any]) -> None:
        out = policy.check_container_create(_sdk_create_body(**kwargs))
        assert out["Labels"][MANAGED_LABEL] == "true"

    def test_label_cannot_be_spoofed_off(self, policy: DockerApiPolicy) -> None:
        body = _sdk_create_body(**_LEGACY_RUN, labels={MANAGED_LABEL: "false", "keep": "1"})
        out = policy.check_container_create(body)
        assert out["Labels"] == {MANAGED_LABEL: "true", "keep": "1"}

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
            policy.check_container_create(_sdk_create_body(**{**_LEGACY_RUN, **extra}))

    @pytest.mark.parametrize("network_kwargs", [
        {"network_mode": "host"},
        {"network_mode": "bridge"},
        {"network_mode": "container:pipeshub-ai"},
        {"network": "pipeshub-ai_pipeshub"},
        {},  # SDK default is NetworkMode=default (the docker0 bridge)
    ])
    def test_refuses_other_networks(self, policy: DockerApiPolicy, network_kwargs: dict[str, Any]) -> None:
        base = {k: v for k, v in _LEGACY_RUN.items() if k not in ("network_mode", "network_disabled")}
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_sdk_create_body(**base, **network_kwargs))

    def test_refuses_other_image(self, policy: DockerApiPolicy) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_container_create(_sdk_create_body(**{**_LEGACY_RUN, "image": "alpine"}))

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

    @pytest.mark.parametrize("log_config", [
        {"Type": "json-file", "Config": {}}, {"Type": "local"}, {"Type": "", "Config": None},
    ])
    def test_admits_local_log_drivers(self, policy: DockerApiPolicy, log_config: dict[str, Any]) -> None:
        body = {"Image": IMAGE, "HostConfig": {"NetworkMode": "none", "LogConfig": log_config}}
        assert policy.check_container_create(body)["Labels"][MANAGED_LABEL] == "true"

    def test_label_keys_are_a_map_and_keep_their_case(self, policy: DockerApiPolicy) -> None:
        body = {"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}, "Labels": {"a": "1", "A": "2"}}
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
        body = _sdk_network_body(
            name=EGRESS, driver="bridge", internal=False, enable_ipv6=False,
            options={"com.docker.network.bridge.enable_icc": "false"},
            labels={"pipeshub.sandbox": "egress"}, check_duplicate=True,
        )
        assert policy.check_network_create(body) == body

    def test_refuses_ipv6(self, policy: DockerApiPolicy) -> None:
        with pytest.raises(PolicyDenied):
            policy.check_network_create(_sdk_network_body(name=EGRESS, driver="bridge", enable_ipv6=True))

    def test_admits_egress_network(self, policy: DockerApiPolicy) -> None:
        body = {"Name": EGRESS, "Driver": "bridge", "Options": {"com.docker.network.bridge.enable_icc": "false"},
                "IPAM": None, "CheckDuplicate": True, "Labels": {"pipeshub.sandbox": "egress"}}
        assert policy.check_network_create(body) == body

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
        if path.endswith("/json") and "/containers/" in path:
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


async def _request(port: int, raw: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data


@pytest.fixture
async def proxied(tmp_path, policy: DockerApiPolicy) -> AsyncIterator[tuple[_FakeDaemon, int]]:
    owned = {"Id": "f" * 64, "Config": {"Labels": {MANAGED_LABEL: "true"}}}
    foreign = {"Id": "e" * 64, "Config": {"Labels": {}}}
    daemon = _FakeDaemon(
        {"owned": owned, "f" * 64: owned, "foreign": foreign},
        networks={"egressid": {"Name": EGRESS}, "appnetid": {"Name": "pipeshub-ai_pipeshub"}},
    )
    sock = str(tmp_path / "d.sock")
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    proxy = DockerSocketProxy(policy, sock)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield daemon, port
    server.close()
    upstream.close()


class TestProxyServer:
    async def test_ping_is_forwarded_with_connection_close(self, proxied) -> None:
        daemon, port = proxied
        resp = await _request(port, b"GET /_ping HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n\r\n")
        assert resp.startswith(b"HTTP/1.1 200")
        assert b"Connection: close" in resp
        assert daemon.requests[0][:2] == ("GET", "/_ping")

    async def test_create_is_rewritten_with_label(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps({"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}}).encode()
        resp = await _request(port, b"POST /v1.43/containers/create HTTP/1.1\r\nHost: x\r\n"
                              b"Content-Type: application/json\r\nContent-Length: "
                              + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert resp.startswith(b"HTTP/1.1 200")
        sent = json.loads(daemon.requests[-1][2])
        assert sent["Labels"][MANAGED_LABEL] == "true"

    async def test_chunked_create_body_is_read(self, proxied) -> None:
        daemon, port = proxied
        body = json.dumps({"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}}).encode()
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

    @pytest.mark.parametrize("name", ["foreign", "missing"])
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

    async def test_chunked_response_is_passed_through(self, proxied) -> None:
        _, port = proxied
        resp = await _request(port, b"GET /containers/owned/logs?stdout=1 HTTP/1.1\r\nHost: x\r\n\r\n")
        head, _, body = resp.partition(b"\r\n\r\n")
        assert b"Connection: close" in head and b"keep-alive" not in head
        assert body == b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"

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
        body = json.dumps({"Image": IMAGE, "HostConfig": {"NetworkMode": "none"}}).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"POST /containers/create HTTP/1.1\r\nHost: x\r\nExpect: 100-continue\r\n"
                     b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n")
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


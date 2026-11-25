"""A real egress proxy, a fake gateway and a target service as ``pxh-`` containers, for P5.4's Docker lanes.

The proxy runs this worktree's ``harness_core`` (bind-mounted read-only) with the
test venv's site-packages, in ``python:3.12-slim``; it must be a container
because a sandbox's isolated network has no route to the host. The gateway
records every ``Authorization`` it receives on stdout and answers with it, so a
test sees both the injected token and the proxy's scrubbing. Everything carries
``pipeshub.harness.owner=x-lead`` and is removed by name on teardown, and ``docker_session``'s labels (suite
``SUITE``), so a process that ends before its teardown removes them on its way out (SBXLEAK).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sysconfig
import tempfile
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from harness_core.sandbox.egress.addresses import DEFAULT_SUBNET_POOL
from harness_core.sandbox.egress.tokens import InMemoryTokenSource
from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT, EGRESS_PROXY_PORT
from harness_core.testing.sandbox.contract import EgressContractEnv
from tests.support import docker_session
from tests.support.docker_networks import (
    EGRESS_CONTROL_ALIAS,
    control_alias_config,
    create_rig_network,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.sandbox.egress.client import RemoteEgressBindings

OWNER = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "P5.4"}
SUITE = "p54-egress-stack"
HELPER_IMAGE = "python:3.12-slim"
REDIS_IMAGE = "redis:7.4-bookworm"
PYTHON_ROOT = Path(__file__).resolve().parents[4]
GATEWAY_PORT = 8080
TARGET_PORT = 8080

_GATEWAY = r"""
import hashlib, json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def _any(self):
        auth = self.headers.get("Authorization", "")
        key = hashlib.sha256(self.headers.get("X-Api-Key", "").encode()).hexdigest()
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        print("AUTH " + json.dumps({"auth": auth, "path": self.path, "capped": self.headers.get("X-Egress-Capped", "")}), flush=True)
        body = json.dumps({"auth": auth, "path": self.path, "keyDigest": key}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Echo", auth)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_POST = do_PUT = _any
    def log_message(self, *a):
        pass
HTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
"""
_PROXY = (
    "import sys, runpy; sys.path.insert(0, '/src'); sys.path.append('/site'); "
    "runpy.run_module('harness_core.sandbox.egress', run_name='__main__')"
)
# P5.4b: the same core with tunnels and Redis state (``egress_entry``).
_PROXY_B = (
    "import sys, runpy; sys.path.insert(0, '/src'); sys.path.append('/site'); "
    "runpy.run_module('tests.unit.harness.sandbox_broker.egress_entry', run_name='__main__')"
)
TLS_PORT = 8443
# A TLS server for one name: answers every request with the request head it received (opaque-tunnel check).
_TLS = r"""
import socket, ssl, sys
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("/pki/cert.pem", "/pki/key.pem")
srv = socket.create_server(("0.0.0.0", int(sys.argv[1])))
while True:
    conn, _ = srv.accept()
    try:
        tls = ctx.wrap_socket(conn, server_side=True)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = tls.recv(4096)
            if not chunk:
                break
            head += chunk
        print("TLS " + head.split(b"\r\n", 1)[0].decode("latin-1"), flush=True)
        tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(head), head))
        tls.close()
    except Exception as exc:
        print("ERR " + type(exc).__name__, flush=True)
        conn.close()
"""


# G-21 (SEC-EG-13): an upstream forward proxy that wants ``Proxy-Authorization: Basic <argv[2]>``. It logs each request
# line and whether the credential matched, answers plain HTTP itself (echoing the request line it got, so the test sees
# what the egress proxy asked for), and for CONNECT answers 200 and echoes the first bytes back: no route off the host.
UPSTREAM_PROXY_PORT = 3128
_UPSTREAM_PROXY = r"""
import asyncio, base64, sys
expected = "Basic " + base64.b64encode(sys.argv[2].encode()).decode()
async def handle(reader, writer):
    try:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        lines = head.split("\r\n")
        auth = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("proxy-authorization:")), "")
        verdict = "ok" if auth == expected else ("missing" if not auth else "wrong")
        print("UPROXY " + lines[0] + " AUTH " + verdict, flush=True)
        if verdict != "ok":
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        elif lines[0].startswith("CONNECT "):
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            writer.write(b"tunnel-echo:" + await reader.read(1024))
        else:
            body = ("chained " + lines[0]).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
        await writer.drain()
    except Exception as exc:
        print("ERR " + type(exc).__name__, flush=True)
    finally:
        writer.close()
async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", int(sys.argv[1]))
    print("LISTENING", flush=True)
    await server.serve_forever()
asyncio.run(main())
"""


@dataclass
class EgressStack:
    client: Any  # docker.DockerClient
    suffix: str
    network: str
    proxy: str
    gateway: str
    target: str
    control_token: str
    secret: bytes
    # P5.4b: a TLS target, a Redis for the proxy's state, the scratch folder holding the TLS key.
    tls: str | None = None
    redis: str | None = None
    pki_dir: str | None = None
    # WP-14: further containers on the rig network a test started (``start_decoy``), removed with the stack.
    extras: list[str] = field(default_factory=list[str])
    # The rig network's gateway: the Docker host's address on it.
    rig_gateway: str = ""
    # G-21: the upstream forward proxy (``StackOptions.upstream_proxy``).
    upstream_proxy: str | None = None

    @property
    def control_url(self) -> str:
        return f"http://{self.address(self.proxy)}:{EGRESS_CONTROL_PORT}"

    def address(self, container: str) -> str:
        info = self.client.api.inspect_container(container)
        return info["NetworkSettings"]["Networks"][self.network]["IPAddress"]

    def logs(self, container: str) -> str:
        return self.client.api.logs(container).decode("utf-8", "replace")

    def gateway_authorizations(self) -> list[str]:
        return [
            json.loads(line[len("AUTH ") :])["auth"]
            for line in self.logs(self.gateway).splitlines()
            if line.startswith("AUTH ")
        ]

    async def wait_for_authorization(self, authorization: str, *, timeout_s: float = 10.0) -> bool:
        # The daemon copies container output to its log asynchronously.
        deadline = time.monotonic() + timeout_s
        while authorization not in self.gateway_authorizations():
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.2)
        return True

    def gateway_marks(self) -> list[tuple[str, str]]:
        """(path, ``X-Egress-Capped``) of each request the gateway received, oldest first."""
        return [
            (entry["path"], entry.get("capped", ""))
            for entry in (json.loads(line[len("AUTH ") :]) for line in self.logs(self.gateway).splitlines() if line.startswith("AUTH "))
        ]

    def gateway_requests(self) -> int:
        return sum(1 for line in self.logs(self.gateway).splitlines() if line.startswith("AUTH "))

    def restart_proxy(self) -> None:
        self.client.api.restart(self.proxy, timeout=5)
        _wait_healthy(self.control_url)


def _run(client: Any, name: str, command: list[str], *, env: Mapping[str, str] | None = None,  # noqa: ANN401
         binds: Mapping[str, str] | None = None, network: str, extra_hosts: Mapping[str, str] | None = None,
         image: str = HELPER_IMAGE, networking_config: Any = None) -> None:  # noqa: ANN401
    host_config = client.api.create_host_config(
        binds={src: {"bind": dst, "mode": "ro"} for src, dst in (binds or {}).items()},
        network_mode=network,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        read_only=True,
        mem_limit=256 * 1024 * 1024,
        tmpfs={"/tmp": "size=16m"},
        extra_hosts=dict(extra_hosts or {}),
    )
    try:
        created = client.api.create_container(
            image, command=command, name=name, user="1000:1000", environment=dict(env or {}),
            labels=_labels(), host_config=host_config, networking_config=networking_config,
        )
        client.api.start(created["Id"])
    except Exception:
        # A create the daemon finished after a client timeout leaves the name taken: remove it by name.
        with contextlib.suppress(Exception):
            client.api.remove_container(name, force=True)
        raise


def _labels() -> dict[str, str]:
    return {**OWNER, **docker_session.labels(SUITE)}


def _wait_healthy(url: str, *, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"egress proxy at {url} never became healthy")
        time.sleep(0.3)


@dataclass(frozen=True)
class StackOptions:
    """P5.4b: what the proxy runs with beyond P5.4's stack."""

    tunnels: bool = False
    # PEM (cert, key) for a TLS target serving one name on ``TLS_PORT``.
    tls_pki: tuple[bytes, bytes] | None = None
    redis_state: bool = False
    # Names the proxy's own resolver answers (its /etc/hosts), given the stack: DNS rebinding cases.
    proxy_hosts: Callable[[EgressStack], Mapping[str, str]] | None = None
    # SIGNFIX: tokens only for the run attempt a binding names, as Node issues them (``AttemptTokenSource``).
    attempt_tokens: bool = False
    # WP-14 (C6.14): the proxy starts with the operator's incident stop on.
    kill_switch: bool = False
    # G-21: start an upstream forward proxy wanting this ``user:password`` and point the egress proxy at it.
    upstream_proxy: str | None = None
    # G-21: further egress-proxy environment, given the stack (``EGRESS_PRIVATE_ALLOW`` names the stack's addresses).
    proxy_env: Callable[[EgressStack], Mapping[str, str]] | None = None


def start_stack(client: Any, options: StackOptions | None = None) -> EgressStack:  # noqa: ANN401
    options = options or StackOptions()
    suffix = uuid.uuid4().hex[:8]
    stack = EgressStack(
        client=client,
        suffix=suffix,
        network=f"pxh-p54-up-{suffix}",
        proxy=f"pxh-p54-proxy-{suffix}",
        gateway=f"pxh-p54-gw-{suffix}",
        target=f"pxh-p54-target-{suffix}",
        control_token=uuid.uuid4().hex,
        secret=uuid.uuid4().hex.encode(),
    )
    stack.rig_gateway = create_rig_network(client, stack.network, labels=_labels())
    try:
        _run(client, stack.gateway, ["python", "-c", _GATEWAY, str(GATEWAY_PORT)], network=stack.network)
        _run(client, stack.target, ["python", "-m", "http.server", str(TARGET_PORT)], network=stack.network)
        env = {"PYTHONDONTWRITEBYTECODE": "1"}
        if options.tls_pki is not None:
            stack.tls, stack.pki_dir = f"pxh-p54-tls-{suffix}", tempfile.mkdtemp(prefix="pxh-p54b-pki-")
            folder = Path(stack.pki_dir)
            folder.chmod(0o755)
            for name, pem in zip(("cert.pem", "key.pem"), options.tls_pki, strict=True):
                (folder / name).write_bytes(pem)
                (folder / name).chmod(0o644)
            _run(client, stack.tls, ["python", "-c", _TLS, str(TLS_PORT)], network=stack.network,
                 binds={stack.pki_dir: "/pki"})
        if options.redis_state:
            stack.redis = f"pxh-p54-redis-{suffix}"
            _run(client, stack.redis, ["redis-server", "--save", "", "--appendonly", "no"], network=stack.network,
                 image=REDIS_IMAGE)
            env |= {"EGRESS_TEST_REDIS_URL": f"redis://{stack.redis}:6379/0", "EGRESS_TEST_STATE_KEY": uuid.uuid4().hex}
        if options.tunnels:
            env["EGRESS_TEST_TUNNELS"] = "1"
        if options.attempt_tokens:
            env["EGRESS_TEST_ATTEMPT_TOKENS"] = "1"
        if options.kill_switch:
            env["AGENT_HARNESS_KILL_SWITCH"] = "true"
        if options.upstream_proxy is not None:
            stack.upstream_proxy = f"pxh-p54-uproxy-{suffix}"
            _run(client, stack.upstream_proxy,
                 ["python", "-c", _UPSTREAM_PROXY, str(UPSTREAM_PROXY_PORT), options.upstream_proxy],
                 network=stack.network)
            _wait_for_log(stack, stack.upstream_proxy, "LISTENING")
            env |= {
                "EGRESS_UPSTREAM_PROXY": f"http://{stack.upstream_proxy}:{UPSTREAM_PROXY_PORT}",
                "EGRESS_UPSTREAM_PROXY_AUTH": options.upstream_proxy,
            }
        if options.proxy_env is not None:
            env |= dict(options.proxy_env(stack))
        entry = (
            options.tunnels or options.redis_state or options.attempt_tokens or options.kill_switch
            or options.upstream_proxy is not None or options.proxy_env is not None
        )
        upstreams = {
            "model-gateway": f"http://{stack.gateway}:{GATEWAY_PORT}",
            "tool-gateway": f"http://{stack.gateway}:{GATEWAY_PORT}/tools",
        }
        _run(
            client,
            stack.proxy,
            ["python", "-c", _PROXY_B if entry else _PROXY],
            env={
                "EGRESS_CONTROL_TOKEN": stack.control_token,
                # The control plane listens on the rig's network only, never on a sandbox's (E2E4).
                "EGRESS_CONTROL_HOST": EGRESS_CONTROL_ALIAS,
                "SANDBOX_EGRESS_SUBNETS": DEFAULT_SUBNET_POOL,
                "EGRESS_TOKEN_SECRET": stack.secret.decode(),
                # WP-06: sealed credentials (MCP runners' secrets, the legacy sandbox's registry and database).
                "EGRESS_SEALING_SECRET": stack.secret.decode(),
                "EGRESS_UPSTREAMS": json.dumps(upstreams),
                **env,
            },
            binds={str(PYTHON_ROOT): "/src", sysconfig.get_paths()["purelib"]: "/site"},
            network=stack.network,
            extra_hosts=options.proxy_hosts(stack) if options.proxy_hosts is not None else None,
            networking_config=control_alias_config(client, stack.network),
        )
        _wait_healthy(stack.control_url)
    except BaseException:
        stop_stack(stack)
        raise
    return stack


def stop_stack(stack: EgressStack) -> None:
    api = stack.client.api
    for name in (stack.proxy, stack.gateway, stack.target, stack.tls, stack.redis, stack.upstream_proxy, *stack.extras):
        if name is None:
            continue
        with contextlib.suppress(Exception):
            api.remove_container(name, force=True, v=True)
    with contextlib.suppress(Exception):
        api.remove_network(stack.network)
    if stack.pki_dir is not None:
        shutil.rmtree(stack.pki_dir, ignore_errors=True)


# Listens on every port it is given and accepts and closes each connection: a stand-in for PipesHub's own stores
# and services on the deployment's network, so a probe from a sandbox has something real to reach (SEC-ISO-09).
_DECOY = r"""
import asyncio, sys
async def main():
    async def accept(reader, writer):
        writer.close()
    servers = [await asyncio.start_server(accept, "0.0.0.0", int(port)) for port in sys.argv[1:]]
    print("LISTENING", len(servers), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


def _wait_for_log(stack: EgressStack, container: str, line: str, *, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while line not in stack.logs(container):
        if time.monotonic() > deadline:
            raise TimeoutError(f"{container} never logged {line!r}")
        time.sleep(0.2)


def start_decoy(stack: EgressStack, name: str, ports: list[int]) -> str:
    """A ``pxh-`` container on the stack's rig network listening on ``ports``; removed by ``stop_stack``."""
    container = f"pxh-p54-{name}-{stack.suffix}"
    stack.extras.append(container)
    _run(stack.client, container, ["python", "-c", _DECOY, *map(str, ports)], network=stack.network)
    deadline = time.monotonic() + 30
    while "LISTENING" not in stack.logs(container):
        if time.monotonic() > deadline:
            raise TimeoutError(f"{container} never listened")
        time.sleep(0.2)
    return container


def rig_probe(stack: EgressStack, container: str, host: str, port: int) -> str:
    """``tcp_probe`` run in a rig container (the positive control: the address answers off the sandbox plane)."""
    result = stack.client.api.exec_create(container, tcp_probe(host, port), user="1000:1000")
    return stack.client.api.exec_start(result["Id"]).decode("utf-8", "replace").strip()


def sandbox_curl(url: str, headers: Mapping[str, str], *, via_proxy: bool = True, method: str = "GET",
                 data: str | None = None) -> list[str]:
    """curl inside the sandbox; prints ``<status> <X-Egress-Refusal or nothing>``, a newline, the body."""
    args = ["-s", "-m", "10", "-o", "/tmp/.egress-body", "-w", "%{http_code} %header{x-egress-refusal}", "-X", method]
    if via_proxy:
        args += ["--proxy", f"http://egress-proxy:{EGRESS_PROXY_PORT}"]
    for key, value in headers.items():
        args += ["-H", f"{key}: {value}"]
    if data is not None:
        args += ["--data-binary", data]
    script = 'curl "$@"; echo; cat /tmp/.egress-body 2>/dev/null; rm -f /tmp/.egress-body'
    return ["/bin/sh", "-c", script, "sh", *args, url]


_PROBE = r"""
import socket, sys
s = socket.socket(socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET)
s.settimeout(float(sys.argv[3]))
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
    print("open")
except Exception as exc:
    print(type(exc).__name__, getattr(exc, "errno", None))
"""

_RESOLVE = r"""
import socket, sys
try:
    print("resolved", socket.getaddrinfo(sys.argv[1], 80)[0][4][0])
except Exception as exc:
    print(type(exc).__name__)
"""

# Reads the secret from stdin, so it never appears in its own argv or environment.
_SCAN = r"""
import os, sys
secret = sys.stdin.read().strip().encode()
hits = []
me = str(os.getpid())
for top in ("/tmp", "/work", "/home", "/run", "/dev/shm"):
    for root, _, files in os.walk(top):
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as f:
                    if secret in f.read():
                        hits.append(path)
            except OSError:
                pass
for pid in os.listdir("/proc"):
    if not pid.isdigit() or pid == me:
        continue
    for part in ("environ", "cmdline"):
        try:
            with open(f"/proc/{pid}/{part}", "rb") as f:
                if secret in f.read():
                    hits.append(f"/proc/{pid}/{part}")
        except OSError:
            pass
if any(secret in v.encode() for v in os.environ.values()):
    hits.append("env")
print("hits", len(hits), *hits)
"""


def tcp_probe(host: str, port: int, *, timeout_s: float = 3.0) -> list[str]:
    """Prints ``open`` or the connect error's type and errno."""
    return ["python3", "-c", _PROBE, host, str(port), str(timeout_s)]


def resolve(host: str) -> list[str]:
    return ["python3", "-c", _RESOLVE, host]


def scan_for_secret() -> list[str]:
    """Run with stdin: write the secret, close stdin; prints ``hits <n> <where>...``."""
    return ["python3", "-c", _SCAN]


class DockerEgressContractEnv(EgressContractEnv):
    def __init__(self, stack: EgressStack, bindings: RemoteEgressBindings) -> None:
        self._stack = stack
        self._bindings = bindings

    @override
    def argv_http(self, url: str, headers: Mapping[str, str]) -> list[str]:
        return sandbox_curl(url, headers)

    @override
    async def gateway_received(self, authorization: str) -> bool:
        return await self._stack.wait_for_authorization(authorization)

    @override
    def expected_token(self, sandbox_id: str) -> str:
        return InMemoryTokenSource.derive(self._stack.secret, sandbox_id, self.credential_ref, 0)

    @override
    async def bound(self) -> frozenset[str]:
        return await self._bindings.bound()


__all__ = [
    "OWNER",
    "REDIS_IMAGE",
    "TLS_PORT",
    "UPSTREAM_PROXY_PORT",
    "StackOptions",
    "DockerEgressContractEnv",
    "EgressStack",
    "resolve",
    "sandbox_curl",
    "scan_for_secret",
    "rig_probe",
    "start_decoy",
    "start_stack",
    "stop_stack",
    "tcp_probe",
]

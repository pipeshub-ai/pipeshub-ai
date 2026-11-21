"""No-proxy confinement probes (E2E3 NP-1..NP-5, cohesion C-28).

C-13 shows that a sandbox's gateway traffic goes through the egress proxy. These probes show that nothing else
leaves, without trusting the proxy settings: the probe unsets every proxy variable and dials directly. The script is
``bash`` only (``/dev/tcp``, ``/dev/udp``, ``timeout``, ``od``, ``getent``) because the Codex and Claude Code images
carry no Python. Each probe reports what happened and the test decides; a probe that errors reports ``closed`` only
when the connect itself failed, so a broken probe cannot read as confined (C-28's negative control proves the probe
does connect where a route exists).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from harness_core.sandbox.ports import SandboxHandle

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
PUBLIC_TCP = (("1.1.1.1", 443), ("8.8.8.8", 53), ("93.184.215.14", 80), ("2606:4700:4700::1111", 443))
PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8", "168.63.129.16")
METADATA = (("169.254.169.254", 80), ("fd00:ec2::254", 80), ("168.63.129.16", 80), ("168.63.129.16", 32526))
PUBLIC_NAME = "example.com"
EMBEDDED_RESOLVER = "127.0.0.11"
SCAN_PORTS = (22, 53, 80, 443, 2375, 3000, 3002, 6379, 7687, 8080, 8097, 8098)

_TCP_LINE = re.compile(r"TCP (\S+) (\d+) (open|closed)")
# A DNS query for example.com/A (id 0x1234, recursion desired).
_QUERY = r"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01"

SCRIPT = r"""
# Job-control notices ("Terminated" when a timeout fires) go to stderr; a reader merging the streams would see them
# glued to a result line.
exec 2>/dev/null
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy
for v in HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy; do
  if [ -n "${!v+x}" ]; then echo "ENV $v set"; fi
done
tcp() {
  if timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; then echo "TCP $1 $2 open"; else echo "TCP $1 $2 closed"; fi
}
udp53() {
  local out
  out=$(timeout 4 bash -c "exec 3<>/dev/udp/$1/53 && printf '@QUERY@' >&3 && timeout 3 od -An -tu1 -N12 <&3" 2>/dev/null | tr -s ' \n' ' ')
  if [ -z "${out// /}" ]; then echo "UDP53 $1 silent"; else echo "UDP53 $1 answered $out"; fi
}
tcp53() {
  local out
  out=$(timeout 4 bash -c "exec 3<>/dev/tcp/$1/53 && printf '\x00\x1d@QUERY@' >&3 && timeout 3 od -An -tu1 -N14 <&3" 2>/dev/null | tr -s ' \n' ' ')
  if [ -z "${out// /}" ]; then echo "TCP53 $1 silent"; else echo "TCP53 $1 answered $out"; fi
}
http() {
  local out
  out=$(timeout 4 bash -c "exec 3<>/dev/tcp/$1/$2 && printf 'GET /healthz HTTP/1.0\r\nHost: probe\r\n\r\n' >&3 && timeout 3 cat <&3" 2>/dev/null | tr -d '\r' | tr '\n' ' ' | cut -c1-400)
  echo "HTTP $1 $2 ${out:-silent}"
}
# A sandbox has a small pid limit: probe in batches.
jobs_running=0
bg() { "$@" & jobs_running=$((jobs_running + 1)); if [ "$jobs_running" -ge 12 ]; then wait; jobs_running=0; fi; }
if timeout 5 getent hosts @NAME@ >/dev/null 2>&1; then echo "DNS @NAME@ resolved"; else echo "DNS @NAME@ unresolved"; fi
for ns in $(awk '/^nameserver/ {print $2}' /etc/resolv.conf 2>/dev/null) @RESOLVERS@; do
  echo "RESOLVER $ns"
  bg udp53 "$ns"
  bg tcp53 "$ns"
  bg tcp "$ns" 53
done
@PROBES@
wait
@HTTP@
wait
echo "DONE"
"""


def script(targets: Iterable[tuple[str, int]], http: Iterable[tuple[str, int]] = ()) -> str:
    """The probe for ``targets`` (host, port) on top of the fixed public, DNS and metadata probes; ``http``: ports
    that answer TCP and must refuse an HTTP request too (what they say is reported)."""
    every = dict.fromkeys([*PUBLIC_TCP, *METADATA, *targets])
    probes = "\n".join(f"bg tcp {host} {port}" for host, port in every)
    asks = "\n".join(f"bg http {host} {port}" for host, port in http)
    return (
        SCRIPT.replace("@QUERY@", _QUERY)
        .replace("@NAME@", PUBLIC_NAME)
        .replace("@RESOLVERS@", " ".join(PUBLIC_RESOLVERS))
        .replace("@PROBES@", probes)
        .replace("@HTTP@", asks)
    )


@dataclass(frozen=True)
class ProbeReport:
    lines: tuple[str, ...]
    # Every (host, port) the script was asked to dial: one without an answer line is a violation.
    asked: tuple[tuple[str, int], ...] = ()

    def unanswered(self) -> list[tuple[str, int]]:
        seen = self.open_tcp() | self.closed_tcp()
        return [target for target in self.asked if target not in seen]

    def _tcp(self, state: str) -> set[tuple[str, int]]:
        return {
            (m.group(1), int(m.group(2))) for line in self.lines for m in _TCP_LINE.finditer(line) if m.group(3) == state
        }

    def open_tcp(self) -> set[tuple[str, int]]:
        return self._tcp("open")

    def closed_tcp(self) -> set[tuple[str, int]]:
        return self._tcp("closed")

    def dns_answers(self) -> list[str]:
        """Resolvers that answered the public query with a record (ANCOUNT non-zero) or with something that is not
        a DNS header: either way the name may have left. ``RCODE`` alone (SERVFAIL, REFUSED) is a refusal."""
        answered: list[str] = []
        for line in self.lines:
            parts = line.split()
            if parts[:1] not in (["UDP53"], ["TCP53"]) or parts[2] != "answered":
                continue
            raw = [int(b) for b in parts[3:]]
            header = raw[2:] if parts[0] == "TCP53" else raw
            if len(header) < 8 or header[:2] != [0x12, 0x34] or (header[6] << 8 | header[7]) > 0:
                answered.append(f"{parts[0]} {parts[1]}")
        return answered

    def dns_refusals(self) -> dict[str, int]:
        """``protocol resolver`` -> the RCODE of each resolver that answered without records."""
        codes: dict[str, int] = {}
        for line in self.lines:
            parts = line.split()
            if parts[:1] in (["UDP53"], ["TCP53"]) and parts[2] == "answered":
                raw = [int(b) for b in parts[3:]]
                header = raw[2:] if parts[0] == "TCP53" else raw
                if len(header) >= 4:
                    codes[f"{parts[0]} {parts[1]}"] = header[3] & 0x0F
        return codes

    def http(self, host: str, port: int) -> str:
        prefix = f"HTTP {host} {port} "
        return next((line.removeprefix(prefix) for line in self.lines if line.startswith(prefix)), "")

    def resolved(self) -> bool:
        return any(line == f"DNS {PUBLIC_NAME} resolved" for line in self.lines)

    def proxy_variables_left(self) -> list[str]:
        return [line.split()[1] for line in self.lines if line.startswith("ENV ")]

    @property
    def complete(self) -> bool:
        return "DONE" in self.lines


async def probe(
    handle: SandboxHandle,
    targets: Iterable[tuple[str, int]],
    *,
    http: Iterable[tuple[str, int]] = (),
    timeout_s: float = 120,
) -> ProbeReport:
    """Run the probe in ``handle`` as its sandbox user; the exec's environment names every proxy variable as set,
    so the script's own ``unset`` is what is under test, not an empty environment."""
    asked = tuple(dict.fromkeys([*PUBLIC_TCP, *METADATA, *targets]))
    process = await handle.exec(["bash", "-c", script(asked, http)], env=_poisoned())
    out = bytearray()

    async def take() -> None:
        async for chunk in process.stdout:
            out.extend(chunk)

    async def drop() -> None:
        async for _ in process.stderr:
            pass

    _ = await asyncio.wait_for(asyncio.gather(take(), drop()), timeout_s)
    _ = await process.wait(timeout_s=30)
    lines = tuple(line.strip() for line in out.decode("utf-8", "replace").splitlines() if line.strip())
    return ProbeReport(lines, asked)


def _poisoned() -> dict[str, str]:
    return dict.fromkeys([*PROXY_VARIABLES, *(v.lower() for v in PROXY_VARIABLES)], "http://egress-proxy:8097")


def exec_in(client: Any, container: str, command: str, *, env: Mapping[str, str] | None = None) -> str:  # noqa: ANN401
    """``bash -c command`` in a running sandbox as its user (uid 1000), as the engine's own shell would run it."""
    api = client.api
    created = api.exec_create(container, ["bash", "-c", command], user="1000:1000", environment=dict(env or {}))
    return api.exec_start(created["Id"]).decode("utf-8", "replace")


def probe_container(
    client: Any,  # noqa: ANN401 - docker.DockerClient
    container: str,
    targets: Iterable[tuple[str, int]],
    *,
    http: Iterable[tuple[str, int]] = (),
) -> ProbeReport:
    """``probe`` for a sandbox a broker of another process started (an engine run's): through ``docker exec``."""
    asked = tuple(dict.fromkeys([*PUBLIC_TCP, *METADATA, *targets]))
    out = exec_in(client, container, script(asked, http), env=_poisoned())
    return ProbeReport(tuple(line.strip() for line in out.splitlines() if line.strip()), asked)


@dataclass(frozen=True)
class SandboxNetwork:
    """Where the sandbox sits: its address, its network's gateway and prefix (from ``docker inspect``)."""

    address: str
    gateway: str
    prefix: int

    def hosts(self) -> list[str]:
        network = ipaddress.ip_network(f"{self.address}/{self.prefix}", strict=False)
        return [str(h) for h in network.hosts() if str(h) != self.address]


def sandbox_network(container: Mapping[str, Any]) -> SandboxNetwork:
    networks = cast("dict[str, dict[str, Any]]", container["NetworkSettings"]["Networks"])
    (only,) = networks.values()
    return SandboxNetwork(str(only["IPAddress"]), str(only.get("Gateway") or ""), int(only["IPPrefixLen"]))


@dataclass(frozen=True)
class Confinement:
    """What a confined sandbox may reach: the proxy's data port, and nothing else.

    The proxy's control port is closed on the sandbox's network (E2E4: the control plane binds only addresses off
    every sandbox network; ``control_from_sandbox`` remains the application's second layer). One listener answers
    TCP inside the sandbox's own network namespace by construction, and must refuse:

    * Docker's embedded resolver (``127.0.0.11``, every user-defined network has one): on an ``--internal`` network
      it does not forward, so a public name is refused (an RCODE, no answer records) over UDP and TCP.
    """

    proxy_address: str
    proxy_port: int = 8097
    control_port: int = 8098
    # Addresses that must be unreachable besides the scan (the upstream bridge, other containers, the host).
    forbidden: tuple[tuple[str, int], ...] = ()
    allowed_extra: frozenset[tuple[str, int]] = field(default_factory=frozenset[tuple[str, int]])

    def http_targets(self) -> list[tuple[str, int]]:
        return [(self.proxy_address, self.proxy_port)]

    def targets(self, network: SandboxNetwork) -> list[tuple[str, int]]:
        scan = [(host, port) for host in network.hosts() for port in SCAN_PORTS]
        gateway = [(network.gateway, port) for port in SCAN_PORTS] if network.gateway else []
        return [*scan, *gateway, *(("172.17.0.1", p) for p in SCAN_PORTS), *self.forbidden]

    def violations(self, report: ProbeReport) -> list[str]:
        found: list[str] = []
        if not report.complete:
            found.append("the probe did not finish")
        found += [f"no answer from the probe of {h}:{p}" for h, p in report.unanswered()]
        found += [f"proxy variable still set: {v}" for v in report.proxy_variables_left()]
        allowed = {(self.proxy_address, self.proxy_port), *self.allowed_extra}
        if (self.proxy_address, self.control_port) not in report.closed_tcp():
            found.append("the egress proxy's control port is not closed on the sandbox's network")
        refusals = report.dns_refusals()
        if all(refusals.get(f"{proto} {EMBEDDED_RESOLVER}", 0) != 0 for proto in ("UDP53", "TCP53")):
            allowed.add((EMBEDDED_RESOLVER, 53))
        found += [f"reachable: {h}:{p}" for h, p in sorted(report.open_tcp() - allowed)]
        if (self.proxy_address, self.proxy_port) not in report.open_tcp():
            found.append("the egress proxy's port did not answer (the probe cannot tell open from closed)")
        if report.resolved():
            found.append(f"{PUBLIC_NAME} resolved")
        found += [f"resolver answered {PUBLIC_NAME}: {r}" for r in report.dns_answers()]
        return found


def assert_confined(report: ProbeReport, confinement: Confinement, where: str, expected: Sequence[str] = ()) -> None:
    """``expected``: violations known and recorded (a finding not yet fixed); anything else fails."""
    violations = confinement.violations(report)
    unexpected = [v for v in violations if v not in expected]
    assert not unexpected, f"{where}: {unexpected}\n" + "\n".join(report.lines)


__all__ = [
    "PROXY_VARIABLES",
    "Confinement",
    "ProbeReport",
    "SandboxNetwork",
    "assert_confined",
    "exec_in",
    "probe",
    "probe_container",
    "sandbox_network",
    "script",
]

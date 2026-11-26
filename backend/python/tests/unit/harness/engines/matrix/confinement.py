"""E2E3 §2.5-2.6: confinement checks inside a live engine sandbox, while its run is held mid-turn.

Everything runs in the sandbox as its user (``docker exec``, uid 1000, ``bash`` only), as the engine's own shell
would: the no-proxy probes (NP-1..NP-5, ``tests/support/sandbox_confinement``), H6's dot-segment escape (CF-2), an
absolute-form request to a host that is not allowed (CF-4), a direct MCP call with the sandbox's own tools
placeholder (MC-2), and a dump of every writable file and every process's environment and command line for the
secret scan (CF-3).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness_core.security.token_patterns import find_forbidden
from tests.support.sandbox_confinement import (
    Confinement,
    ProbeReport,
    exec_in,
    probe_container,
    sandbox_network,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

TOOLS_HOST = "tool-gateway"
GATEWAY_PORT = 8097
# The status line and the refusal an HTTP answer carries.
_STATUS = re.compile(r"HTTP/1\.[01] (\d{3})")

_REQUEST = r"""
req() {
  exec 3<>"/dev/tcp/$1/$2" || { echo CONNECT_FAILED; return; }
  printf '%b' "$3" >&3
  timeout 8 cat <&3 | head -c 1500
}
"""

_DUMP = r"""
for m in $(awk '$4 ~ /(^|,)rw(,|$)/ && $3 !~ /^(proc|sysfs|cgroup|cgroup2|devpts|mqueue)$/ && $2 !~ /^\/(proc|sys|dev)/ {print $2}' /proc/self/mounts); do
  find "$m" -xdev -type f -size -1024k 2>/dev/null | while IFS= read -r f; do
    echo "== FILE $f"; cat "$f" 2>/dev/null; echo
  done
done
for f in /proc/[0-9]*/environ /proc/[0-9]*/cmdline; do
  echo "== PROC $f"; tr '\0' '\n' < "$f" 2>/dev/null; echo
done
"""

_PLACEHOLDER = r"""TOK=$(for f in /proc/[0-9]*/environ; do tr '\0' '\n' < "$f" 2>/dev/null; done | sed -n 's/^HARNESS_TOOLS_TOKEN=//p' | head -1)"""


def _get(path: str, host: str = f"{TOOLS_HOST}:{GATEWAY_PORT}") -> str:
    return f"GET {path} HTTP/1.1\\r\\nHost: {host}\\r\\nConnection: close\\r\\n\\r\\n"


def status(answer: str) -> int | None:
    found = _STATUS.search(answer)
    return int(found.group(1)) if found else None


@dataclass(frozen=True)
class SandboxChecks:
    container: str
    probes: ProbeReport
    confinement: Confinement
    # CF-2: what each dot-segment request answered.
    escapes: dict[str, str]
    # CF-4: an absolute-form request to a host the sandbox may not reach.
    elsewhere: str
    # MC-2: a direct ``tools/call`` of ``unoffered`` with the sandbox's own placeholder ("" when not asked).
    unoffered: str
    dump: str

    def leaks(self, secrets: Iterable[str]) -> list[str]:
        """Secret values (first 6 characters named) and credential shapes the sandbox holds."""
        found = [f"{s[:6]}..." for s in secrets if s and s in self.dump]
        found += [f"shape:{kind}" for kind in find_forbidden(self.dump)]
        return found


def sandbox_of(client: Any, namespace: str, run_id: str) -> dict[str, Any]:  # noqa: ANN401 - docker.DockerClient
    """The run's live engine sandbox (the broker's own label, the controller's run label)."""
    (found,) = client.api.containers(filters={"label": [f"{namespace}.managed=1", f"runId={run_id}"]})
    return client.api.inspect_container(found["Id"])


def proxy_address(client: Any, proxy: str, sandbox: dict[str, Any]) -> str:  # noqa: ANN401
    (name,) = sandbox["NetworkSettings"]["Networks"]
    return str(client.api.inspect_container(proxy)["NetworkSettings"]["Networks"][name]["IPAddress"])


def check_sandbox(
    client: Any,  # noqa: ANN401 - docker.DockerClient
    namespace: str,
    proxy: str,
    run_id: str,
    *,
    forbidden: tuple[tuple[str, int], ...],
    unoffered: str | None = None,
) -> SandboxChecks:
    sandbox = sandbox_of(client, namespace, run_id)
    container = str(sandbox["Id"])
    confinement = Confinement(proxy_address(client, proxy, sandbox), forbidden=forbidden)
    probes = probe_container(
        client, container, confinement.targets(sandbox_network(sandbox)), http=confinement.http_targets()
    )
    escapes = {
        path: exec_in(client, container, f"{_REQUEST}\nreq {TOOLS_HOST} {GATEWAY_PORT} '{_get(path)}'")
        for path in ("/mcp/../../harness/x", "/mcp/%2e%2e/%2e%2e/x", "/mcp/%2E%2E/x", "/./mcp")
    }
    elsewhere = exec_in(
        client, container,
        f"{_REQUEST}\nreq {TOOLS_HOST} {GATEWAY_PORT} '{_get('http://harness-state:3002/', 'harness-state:3002')}'",
    )
    called = ""
    if unoffered is not None:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": unoffered, "arguments": {}}}
        )
        called = exec_in(
            client, container,
            f"{_REQUEST}\n{_PLACEHOLDER}\nBODY='{body}'\n"
            f"req {TOOLS_HOST} {GATEWAY_PORT} \"POST /mcp HTTP/1.1\\r\\nHost: {TOOLS_HOST}:{GATEWAY_PORT}\\r\\n"
            "Authorization: Bearer $TOK\\r\\nContent-Type: application/json\\r\\n"
            "Accept: application/json, text/event-stream\\r\\nContent-Length: ${#BODY}\\r\\n"
            "Connection: close\\r\\n\\r\\n$BODY\"",
        )
    dump = exec_in(client, container, _DUMP)
    return SandboxChecks(container, probes, confinement, escapes, elsewhere, called, dump)


__all__ = ["SandboxChecks", "check_sandbox", "sandbox_of", "status"]

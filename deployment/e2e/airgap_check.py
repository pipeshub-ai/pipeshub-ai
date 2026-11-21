"""DEV-ONLY: the air-gapped lane's proof that nothing in the stack has a route out (C15.04; E2E_AIRGAP=1).

    python3 airgap_check.py <compose project>

AG-N1  every network of the project, and every network a container of the project is on, is internal (Docker gives
       an internal network no gateway out), except `<project>_ingress`, which only pxh-ingress joins.
AG-N2  from the app container, a TCP connection to a public address and a name lookup of a public registry both fail.
Exit 0 when both hold.
"""

import json
import subprocess
import sys

PROBE = r"""
import socket, sys
out = []
for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
    try:
        socket.create_connection((host, port), 3).close()
        out.append(f"connected to {host}:{port}")
    except OSError:
        pass
try:
    socket.getaddrinfo("registry-1.docker.io", 443)
    out.append("resolved registry-1.docker.io")
except OSError:
    pass
print("; ".join(out))
sys.exit(1 if out else 0)
"""


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def main(project: str) -> int:
    status = 0
    ingress = f"{project}_ingress"
    ids = docker("ps", "-q", "--filter", f"label=com.docker.compose.project={project}").split()
    containers = json.loads(docker("inspect", *ids)) if ids else []
    net_names = {n for c in containers for n in (c["NetworkSettings"]["Networks"] or {})}
    net_names |= set(docker("network", "ls", "--format", "{{.Name}}", "--filter",
                            f"label=com.docker.compose.project={project}").split())
    nets = {n["Name"]: n for n in json.loads(docker("network", "inspect", *sorted(net_names)))}
    routed = sorted(n for n, net in nets.items() if not net["Internal"] and n != ingress)
    on_ingress = sorted(c["Name"].lstrip("/") for c in containers
                        if ingress in (c["NetworkSettings"]["Networks"] or {})
                        and c["Config"]["Labels"].get("com.docker.compose.service") != "pxh-ingress")
    if routed or on_ingress:
        print(f"  FAIL AG-N1 routed networks {routed}; containers on the ingress network {on_ingress}")
        status = 1
    else:
        print(f"  ok   AG-N1 {len(containers)} containers on {len(nets) - (ingress in nets)} internal networks;"
              " only pxh-ingress is on a routed one")
    app = [c["Id"] for c in containers if c["Config"]["Labels"].get("com.docker.compose.service") == "pipeshub-ai"]
    probe = subprocess.run(["docker", "exec", app[0], "python", "-c", PROBE], capture_output=True, text=True) if app else None
    if probe is None or probe.returncode != 0:
        print(f"  FAIL AG-N2 the app reached out: {probe.stdout.strip() if probe else 'no app container'}")
        status = 1
    else:
        print("  ok   AG-N2 the app container can neither connect to a public address nor resolve a public name")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

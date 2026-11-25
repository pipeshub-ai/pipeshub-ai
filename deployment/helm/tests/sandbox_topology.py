"""Where the Docker daemon lives in a rendered chart (D1, HE-1..HE-6).

    python3 sandbox_topology.py <rendered.yaml> <broker|docker> [--netpol]

broker: a sandbox-broker Deployment (one replica, Recreate, its own token-less
ServiceAccount, ClusterIP Service on 8095) holds the privileged DinD sidecar and
the docker-proxy sidecar (R38IDENT-FU-1): the daemon has no TCP listener and its
socket volume is mounted into dind and docker-proxy alone, never the broker, whose
DOCKER_HOST is the proxy's loopback listener and which holds its own credential
there; the app Deployment has neither, nor the
docker-proxy sidecar, and gets SANDBOX_MODE=broker and SANDBOX_BROKER_BACKEND.
docker: the legacy shape, DinD and docker-proxy in the app pod and no broker. --netpol: the broker's NetworkPolicy admits only the app pods on 8095,
the app's policy does not select the broker, and (SEC-D-09, ADR-OPS-04) the broker's egress is DNS, its KV port and
public addresses on 443 only: no rule to any pod or namespace, no address range holding cloud metadata, a private
range or the CGNAT block. netpol_check.py proves the whole graph; this pins the broker's own rules.
"""

from __future__ import annotations

import ipaddress
import sys
from typing import Any

import yaml

APP = "ci-pipeshub-ai"
BROKER = "ci-pipeshub-ai-sandbox-broker"
# What the broker pod (and every legacy open-egress sandbox behind DinD's masquerade) must never reach.
NEVER = ("169.254.169.254", "169.254.170.23", "10.0.0.1", "172.16.0.1", "192.168.0.1", "100.64.0.1",
         "fd00:ec2::254", "fd00::1")
BROKER_EGRESS_PORTS = {53, 443, 6379, 2379}


def _docs(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return [doc for doc in yaml.safe_load_all(handle) if doc]


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    return next((d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == name), None)


def _containers(deployment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in deployment["spec"]["template"]["spec"]["containers"]}


def _env(container: dict[str, Any]) -> dict[str, Any]:
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def check(path: str, mode: str, netpol: bool) -> list[str]:
    docs = _docs(path)
    problems: list[str] = []
    app = _find(docs, "Deployment", APP)
    broker = _find(docs, "Deployment", BROKER)
    if app is None:
        return ["no app Deployment"]
    app_containers = _containers(app)
    app_env = _env(app_containers["pipeshub-ai"])
    if mode == "docker":
        if broker is not None:
            problems.append("legacy docker mode renders a broker")
        if "dind" not in app_containers:
            problems.append("legacy docker mode lost the app pod's dind sidecar")
        if "docker-proxy" not in app_containers:
            problems.append("legacy docker mode lost the app pod's docker-proxy sidecar")
        if app_env.get("SANDBOX_MODE") != "docker":
            problems.append(f"app SANDBOX_MODE={app_env.get('SANDBOX_MODE')!r}")
        return problems

    if "dind" in app_containers:
        problems.append("the app pod still has the dind sidecar")
    if "docker-proxy" in app_containers:
        problems.append("the app pod still has the docker-proxy sidecar")
    if "DOCKER_HOST" in app_env:
        problems.append("the app pod still has DOCKER_HOST")
    if any(v.get("name") in ("dind-storage", "dind-sock") for v in app["spec"]["template"]["spec"].get("volumes", [])):
        problems.append("the app pod still has a dind volume")
    if app_env.get("SANDBOX_MODE") != "broker":
        problems.append(f"app SANDBOX_MODE={app_env.get('SANDBOX_MODE')!r}")
    if app_env.get("SANDBOX_BROKER_BACKEND") != f"http://{BROKER}:8095":
        problems.append(f"app SANDBOX_BROKER_BACKEND={app_env.get('SANDBOX_BROKER_BACKEND')!r}")
    if broker is None:
        return [*problems, "no sandbox-broker Deployment"]

    spec = broker["spec"]
    pod = spec["template"]["spec"]
    containers = _containers(broker)
    if spec.get("replicas") != 1 or spec.get("strategy", {}).get("type") != "Recreate":
        problems.append("broker is not one replica with Recreate")
    dind = containers.get("dind")
    if dind is None or dind.get("securityContext", {}).get("privileged") is not True:
        problems.append("broker pod has no privileged dind sidecar")
    main = containers.get("sandbox-broker")
    if main is None:
        return [*problems, "no sandbox-broker container"]
    if main.get("command") != ["python", "-m", "app.sandbox_broker_main"]:
        problems.append(f"broker command {main.get('command')}")
    env = _env(main)
    proxy = containers.get("docker-proxy")
    if proxy is None:
        return [*problems, "the broker pod has no docker-proxy sidecar"]
    proxy_env = _env(proxy)
    if env.get("DOCKER_HOST") != f"tcp://127.0.0.1:{proxy_env.get('DOCKER_PROXY_PORT')}":
        problems.append(f"broker DOCKER_HOST={env.get('DOCKER_HOST')!r} is not the docker-proxy listener")
    if proxy_env.get("DOCKER_PROXY_HOST") != "127.0.0.1":
        problems.append(f"docker-proxy listens on {proxy_env.get('DOCKER_PROXY_HOST')!r}, not loopback")
    if dind is not None and any("tcp://" in arg for arg in dind.get("args") or []):
        problems.append("the broker pod's dind has a TCP listener")
    mounting = sorted(
        name for name, c in containers.items() if any(m["name"] == "dind-sock" for m in c.get("volumeMounts") or [])
    )
    if mounting != ["dind", "docker-proxy"]:
        problems.append(f"dind-sock is mounted into {mounting}, expected only dind and docker-proxy")
    for holder, holder_env in (("sandbox-broker", env), ("docker-proxy", proxy_env)):
        if "DOCKER_PROXY_BROKER_TOKEN" not in holder_env:
            problems.append(f"{holder} lacks DOCKER_PROXY_BROKER_TOKEN")
    for name in ("SECRET_KEY", "SECRET_KEY_FILE", "PIPESHUB_COMPONENT_KEY", "REDIS_PASSWORD", "DOCKER_PROXY_TOKEN"):
        if name in proxy_env:
            problems.append(f"docker-proxy holds {name}")
    if proxy_env.get("SANDBOX_INSTALL_ID") != env.get("SANDBOX_INSTALL_ID"):
        problems.append("docker-proxy and the broker disagree on the install id")
    # R44STOP-FU-3: the proxy lets the egress firewall's root helper run only from the image the broker runs it from.
    if not proxy_env.get("SANDBOX_DOCKER_IMAGE") or proxy_env.get("SANDBOX_DOCKER_IMAGE") != env.get("SANDBOX_DOCKER_IMAGE"):
        problems.append("docker-proxy and the broker disagree on the firewall helper's image")
    proxy_sc = proxy.get("securityContext") or {}
    if proxy_sc.get("runAsUser") in (None, 0) or proxy_sc.get("runAsNonRoot") is not True:
        problems.append("docker-proxy runs as root")
    if dind is not None and f"--group={proxy_sc.get('runAsGroup')}" not in (dind.get("args") or []):
        problems.append("the daemon's socket group is not the docker-proxy's")
    for name in ("PIPESHUB_COMPONENT_KEY", "KV_STORE_TYPE", "REDIS_HOST", "SANDBOX_DOCKER_IMAGE", "SANDBOX_EGRESS_NETWORK"):
        if name not in env:
            problems.append(f"broker env lacks {name}")
    # ADR-KEY-02 (SEC-D-03): beside a privileged daemon the broker holds no root key and no shared Redis password.
    for name in ("SECRET_KEY", "SECRET_KEY_FILE", "SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT", "REDIS_PASSWORD", "REDIS_URL"):
        if name in env:
            problems.append(f"broker env holds {name}")
    if "SANDBOX_EGRESS_PROXY_CONTAINER" in env:
        problems.append("broker is wired to an egress proxy it cannot share a daemon with")
    if main.get("securityContext", {}).get("privileged"):
        problems.append("the broker container itself is privileged")
    if pod.get("automountServiceAccountToken") is not False:
        problems.append("broker pod mounts a service account token")
    account = _find(docs, "ServiceAccount", BROKER)
    if account is None or account.get("automountServiceAccountToken") is not False:
        problems.append("broker ServiceAccount missing or mounts a token")
    service = _find(docs, "Service", BROKER)
    if service is None or service["spec"].get("type") != "ClusterIP" or [p["port"] for p in service["spec"]["ports"]] != [8095]:
        problems.append("broker Service is not ClusterIP on 8095 only")

    if netpol:
        policy = _find(docs, "NetworkPolicy", BROKER)
        if policy is None:
            problems.append("no broker NetworkPolicy")
        else:
            rules = policy["spec"].get("ingress", [])
            ports = [p["port"] for rule in rules for p in rule.get("ports", [])]
            sources = [
                peer.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/component")
                for rule in rules
                for peer in rule.get("from", [])
            ]
            if ports != [8095] or sources != ["main"]:
                problems.append(f"broker ingress allows {sources} on {ports}")
            problems += broker_egress_problems(policy)
        general = _find(docs, "NetworkPolicy", APP)
        component = (general or {}).get("spec", {}).get("podSelector", {}).get("matchLabels", {})
        if component.get("app.kubernetes.io/component") != "main":
            problems.append("the app's NetworkPolicy does not select only the app pods")
    return problems


def _reaches(block: dict[str, Any], address: str) -> bool:
    ip = ipaddress.ip_address(address)
    net = ipaddress.ip_network(block["cidr"], strict=False)
    if ip.version != net.version or ip not in net:
        return False
    return not any(ip in ipaddress.ip_network(e, strict=False) for e in block.get("except") or []
                   if ipaddress.ip_network(e, strict=False).version == ip.version)


def broker_egress_problems(policy: dict[str, Any]) -> list[str]:
    spec = policy["spec"]
    problems = []
    if spec.get("policyTypes") != ["Ingress", "Egress"]:
        problems.append(f"broker policyTypes {spec.get('policyTypes')}: its egress is open")
    rules = spec.get("egress") or []
    if not rules:
        problems.append("broker has no egress rules (a policyTypes Egress with none is deny-all, but DinD must pull)")
    for index, rule in enumerate(rules):
        peers = rule.get("to") or []
        ports = {p.get("port") for p in rule.get("ports") or []}
        if not peers:
            problems.append(f"broker egress[{index}] has no destination: any address")
        if not ports or None in ports:
            problems.append(f"broker egress[{index}] allows every port")
        elif not ports <= BROKER_EGRESS_PORTS:
            problems.append(f"broker egress[{index}] opens ports {sorted(ports - BROKER_EGRESS_PORTS)}")
        for peer in peers:
            if peer.get("podSelector") == {} and "namespaceSelector" not in peer:
                problems.append(f"broker egress[{index}] reaches every pod in the namespace")
            if peer.get("namespaceSelector") == {}:
                problems.append(f"broker egress[{index}] reaches every namespace")
            if "namespaceSelector" in peer and ports - {53}:
                problems.append(f"broker egress[{index}] reaches another namespace beyond DNS")
            block = peer.get("ipBlock")
            if block:
                hit = [a for a in NEVER if _reaches(block, a)]
                if hit:
                    problems.append(f"broker egress[{index}] ipBlock {block['cidr']} reaches {hit}")
    return problems


if __name__ == "__main__":
    path, mode, *rest = sys.argv[1:]
    found = check(path, mode, "--netpol" in rest)
    for problem in found:
        print(f"!! {problem}")
    sys.exit(1 if found else 0)

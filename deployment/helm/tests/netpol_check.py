"""Who can reach whom in a rendered chart (ADR-OPS-04, SEC-D-09).

    python3 netpol_check.py <rendered.yaml> [--print] [--allow-world-ingress PORT ...]

Evaluates the rendered NetworkPolicies the way the API defines them (a pod is
isolated for a direction once any policy of that type selects it; an allowed
connection needs the source's egress and the destination's ingress to admit
it) over a small world: every pod the chart renders, the DNS pods in
kube-system, an ingress-controller pod, a pod in an unrelated namespace, and
outside addresses (cloud metadata, the EKS Pod Identity agent, a private VPC
host, a public host). It then checks:

- every rendered pod is isolated for Ingress and Egress (the default deny);
- the enumerated allowed paths equal the expected allow-list, derived from each
  pod's role (app, sandbox broker, store, initiate Job);
- no rule widens with ``podSelector: {}``, ``namespaceSelector: {}``, a rule
  with no peers, or a whole-family ipBlock that does not except metadata.

Pod addresses are modelled inside the pod CIDR below, and an ipBlock is taken
to match them, as Calico and Cilium do; that only adds paths, so the check is
the stricter for it.
"""

from __future__ import annotations

import ipaddress
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

NAMESPACE = "default"
POD_CIDR = "10.244.0.0/16"
METADATA = "169.254.169.254"
METADATA_V6 = "fd00:ec2::254"
POD_IDENTITY = "169.254.170.23"
# The stores the chart can run and the label value that names each.
STORE_COMPONENTS = {
    "mongodb": "mongodb", "arbiter": "mongodb", "redis": "redis", "master": "redis", "replica": "redis",
    "qdrant": "qdrant", "neo4j": "neo4j", "arango": "arango", "etcd": "etcd", "kafka": "kafka", "zookeeper": "zookeeper",
}
CLIENT_PORTS = {
    "mongodb": {27017}, "redis": {6379, 26379}, "qdrant": {6333, 6334}, "neo4j": {7687, 7474}, "arango": {8529},
    "etcd": {2379}, "kafka": {9092}, "zookeeper": {2181},
}
# Stores the app never opens itself.
BACKEND_ONLY = {"zookeeper"}


@dataclass(frozen=True)
class Endpoint:
    name: str
    role: str
    namespace: str | None  # None: outside the cluster
    labels: tuple[tuple[str, str], ...]
    ip: str
    ports: tuple[tuple[int, str, str], ...] = ()  # (port, protocol, name)

    @property
    def label_map(self) -> dict[str, str]:
        return dict(self.labels)


@dataclass
class World:
    pods: list[Endpoint]
    others: list[Endpoint]
    policies: list[dict[str, Any]]
    namespaces: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def everything(self) -> list[Endpoint]:
        return [*self.pods, *self.others]


def load(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return [doc for doc in yaml.safe_load_all(handle) if doc]


def _pod_spec(doc: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]] | None:
    kind = doc.get("kind")
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet"}:
        template = doc["spec"]["template"]
    elif kind == "CronJob":
        template = doc["spec"]["jobTemplate"]["spec"]["template"]
    elif kind == "Pod":
        template = doc
    else:
        return None
    return (template.get("metadata") or {}).get("labels") or {}, template["spec"]


def role_of(labels: dict[str, str]) -> str:
    component = labels.get("app.kubernetes.io/component", "")
    name = labels.get("app.kubernetes.io/name", "")
    if component == "main":
        return "app"
    if component == "sandbox-broker":
        return "broker"
    if component == "egress-proxy":
        return "egress-proxy"
    if component == "mongodb-initiate":
        return "mongodb-initiate"
    if name in ("mongodb", "redis"):
        return f"store:{name}"
    if component in STORE_COMPONENTS:
        return f"store:{STORE_COMPONENTS[component]}"
    return f"unknown:{component or name}"


def world(docs: list[dict[str, Any]]) -> World:
    pods: list[Endpoint] = []
    pod_net = ipaddress.ip_network(POD_CIDR)
    for index, doc in enumerate(d for d in docs if _pod_spec(d)):
        labels, spec = _pod_spec(doc) or ({}, {})
        ports = tuple(
            (int(p["containerPort"]), p.get("protocol", "TCP"), p.get("name", ""))
            for c in spec.get("containers") or []
            for p in c.get("ports") or []
        )
        pods.append(Endpoint(
            f"{doc['kind']}/{doc['metadata']['name']}", role_of(labels), NAMESPACE, tuple(sorted(labels.items())),
            str(pod_net[10 + index]), ports,
        ))
    others = [
        Endpoint("kube-dns", "dns", "kube-system", (("k8s-app", "kube-dns"),), "10.244.200.10",
                 ((53, "UDP", "dns"), (53, "TCP", "dns-tcp"))),
        Endpoint("ingress-controller", "ingress", "ingress-nginx", (("app.kubernetes.io/name", "ingress-nginx"),),
                 "10.244.201.10", ((443, "TCP", "https"),)),
        Endpoint("other-namespace-pod", "foreign", "tenant-b", (("app", "other"),), "10.244.202.10",
                 ((8080, "TCP", "http"),)),
        Endpoint("metadata", "metadata", None, (), METADATA, ((80, "TCP", ""),)),
        Endpoint("metadata-v6", "metadata", None, (), METADATA_V6, ((80, "TCP", ""),)),
        Endpoint("pod-identity-agent", "pod-identity", None, (), POD_IDENTITY, ((80, "TCP", ""), (2703, "TCP", ""))),
        Endpoint("node-local-dns", "link-local", None, (), "169.254.20.10", ((53, "UDP", ""),)),
        Endpoint("vpc-host", "private", None, (), "10.0.12.5", ((6379, "TCP", ""), (443, "TCP", ""), (5432, "TCP", ""))),
        Endpoint("cgnat-host", "private", None, (), "100.64.3.4", ((443, "TCP", ""),)),
        Endpoint("public-host", "public", None, (), "93.184.216.34", ((443, "TCP", ""), (80, "TCP", ""), (6379, "TCP", ""))),
        Endpoint("public-host-v6", "public", None, (), "2606:4700::6810:84e5", ((443, "TCP", ""),)),
    ]
    namespaces = {ns: {"kubernetes.io/metadata.name": ns} for ns in (NAMESPACE, "kube-system", "ingress-nginx", "tenant-b")}
    policies = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    return World(pods, others, policies, namespaces)


def selects(selector: dict[str, Any] | None, labels: dict[str, str]) -> bool:
    if selector is None:
        return False
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != str(value):
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr["key"], expr["operator"], [str(v) for v in expr.get("values") or []]
        present = key in labels
        if op == "In" and not (present and labels[key] in values):
            return False
        if op == "NotIn" and present and labels[key] in values:
            return False
        if op == "Exists" and not present:
            return False
        if op == "DoesNotExist" and present:
            return False
    return True


def _in_block(ip: str, block: dict[str, Any]) -> bool:
    address = ipaddress.ip_address(ip)
    net = ipaddress.ip_network(block["cidr"], strict=False)
    if address.version != net.version or address not in net:
        return False
    return not any(
        address in ipaddress.ip_network(e, strict=False)
        for e in block.get("except") or []
        if ipaddress.ip_network(e, strict=False).version == address.version
    )


def peer_matches(peer: dict[str, Any], target: Endpoint, policy_ns: str, w: World) -> bool:
    if "ipBlock" in peer:
        return _in_block(target.ip, peer["ipBlock"])
    if target.namespace is None:
        return False
    if "namespaceSelector" in peer:
        if not selects(peer["namespaceSelector"], w.namespaces.get(target.namespace, {})):
            return False
    elif target.namespace != policy_ns:
        return False
    return selects(peer.get("podSelector", {}), target.label_map) if "podSelector" in peer else True


def port_matches(rule_ports: list[dict[str, Any]] | None, dest: Endpoint, port: int, proto: str) -> bool:
    if not rule_ports:
        return True
    for entry in rule_ports:
        if entry.get("protocol", "TCP") != proto:
            continue
        if "port" not in entry:
            return True
        want = entry["port"]
        if isinstance(want, str) and not want.isdigit():
            if any(n == want and p == port and pr == proto for p, pr, n in dest.ports):
                return True
            continue
        low, high = int(want), int(entry.get("endPort", want))
        if low <= port <= high:
            return True
    return False


def _isolating(w: World, pod: Endpoint, kind: str) -> list[dict[str, Any]]:
    found = []
    for policy in w.policies:
        ns = policy["metadata"].get("namespace", NAMESPACE)
        types = policy["spec"].get("policyTypes") or (["Ingress", "Egress"] if "egress" in policy["spec"] else ["Ingress"])
        if pod.namespace == ns and kind in types and selects(policy["spec"].get("podSelector", {}), pod.label_map):
            found.append(policy)
    return found


def _admits(w: World, subject: Endpoint, other: Endpoint, dest: Endpoint, port: int, proto: str, kind: str) -> bool:
    if subject.namespace != NAMESPACE:
        return True  # nothing outside the release is modelled as isolated
    policies = _isolating(w, subject, kind)
    if not policies:
        return True
    key = "egress" if kind == "Egress" else "ingress"
    peers_key = "to" if kind == "Egress" else "from"
    for policy in policies:
        ns = policy["metadata"].get("namespace", NAMESPACE)
        for rule in policy["spec"].get(key) or []:
            peers = rule.get(peers_key) or []
            if peers and not any(peer_matches(p, other, ns, w) for p in peers):
                continue
            if port_matches(rule.get("ports"), dest, port, proto):
                return True
    return False


def allowed(w: World, src: Endpoint, dst: Endpoint, port: int, proto: str) -> bool:
    return _admits(w, src, dst, dst, port, proto, "Egress") and _admits(w, dst, src, dst, port, proto, "Ingress")


Path = tuple[str, str, int, str]


def paths(w: World) -> set[Path]:
    sources = [*w.pods, *(o for o in w.others if o.role in ("ingress", "foreign", "public"))]
    found: set[Path] = set()
    for src in sources:
        for dst in w.everything:
            if dst is src or NAMESPACE not in (src.namespace, dst.namespace):
                continue
            for port, proto, _ in dst.ports:
                if allowed(w, src, dst, port, proto):
                    found.add((src.name, dst.name, port, proto))
    return found


def expected(w: World, *, world_ingress_ports: frozenset[int] = frozenset(),
             pod_identity: bool = False, broker_kv: str = "redis",
             broker_extra: frozenset[str] = frozenset()) -> set[Path]:
    """The allow-list ADR-OPS-04 promises, from each endpoint's role. ``broker_kv``: the store the broker logs in
    to (its KV_STORE_TYPE)."""
    want: set[Path] = set()

    def ok(src: Endpoint, dst: Endpoint) -> set[int] | None:
        """The ports src may open on dst; None for every port."""
        s, d = src.role, dst.role
        if d == "dns":
            return {53} if src.namespace == NAMESPACE else set()
        if s == "app":
            if d == "app":
                return None
            if d.startswith("store:"):
                store = d.split(":", 1)[1]
                return CLIENT_PORTS[store] if store not in BACKEND_ONLY else set()
            if d == "broker":
                return {8095}
            if d == "pod-identity":
                return None if pod_identity else set()
            if d in ("metadata", "link-local"):
                return set()
            if dst.namespace is None:
                return None  # model APIs, connectors, private services the operator runs
            # Other namespaces are reachable by address through the internet rule, as on the CNIs that match pod
            # IPs to ipBlocks; their own policies decide.
            return None if d in ("ingress", "foreign") else set()
        if s == "broker":
            if d.startswith("store:") and d.split(":", 1)[1] == broker_kv:
                return CLIENT_PORTS[d.split(":", 1)[1]]
            if d == "public":
                return {443}
            if dst.name in broker_extra:
                return None
            return set()
        if s.startswith("store:"):
            if d == s:
                return None
            if s == "store:kafka" and d == "store:zookeeper":
                return {2181}
            return set()
        if s == "mongodb-initiate":
            return {27017} if d == "store:mongodb" else set()
        if s == "ingress":
            return {3001, 8088} if d == "app" else set()
        if s in ("public", "foreign"):
            return set(world_ingress_ports) if d == "app" else set()
        return set()

    sources = [*w.pods, *(o for o in w.others if o.role in ("ingress", "foreign", "public"))]
    for src in sources:
        for dst in w.everything:
            if dst is src or NAMESPACE not in (src.namespace, dst.namespace):
                continue
            ports = ok(src, dst)
            for port, proto, _ in dst.ports:
                if ports is None or port in ports:
                    want.add((src.name, dst.name, port, proto))
    return want


def broker_kv_store(docs: list[dict[str, Any]]) -> str:
    for doc in docs:
        spec = _pod_spec(doc)
        if spec and spec[0].get("app.kubernetes.io/component") == "sandbox-broker":
            for container in spec[1].get("containers") or []:
                for env in container.get("env") or []:
                    if env["name"] == "KV_STORE_TYPE":
                        return str(env.get("value") or "redis")
    return "redis"


def default_deny_gaps(w: World) -> list[str]:
    gaps = []
    for pod in w.pods:
        for kind in ("Ingress", "Egress"):
            if not _isolating(w, pod, kind):
                gaps.append(f"{pod.name} is not isolated for {kind}")
    return gaps


def widening(policies: list[dict[str, Any]], world_ingress: frozenset[int] = frozenset()) -> list[str]:
    """Rules that open more than one component pair: the bypasses an edit would try."""
    found = []
    for policy in policies:
        name = policy["metadata"]["name"]
        app = (policy["spec"].get("podSelector") or {}).get("matchLabels", {}).get("app.kubernetes.io/component") == "main"
        for key, peers_key in (("ingress", "from"), ("egress", "to")):
            for index, rule in enumerate(policy["spec"].get(key) or []):
                where = f"{name} {key}[{index}]"
                peers = rule.get(peers_key) or []
                if not peers:
                    ports = {int(p["port"]) for p in rule.get("ports") or [] if str(p.get("port", "")).isdigit()}
                    fixed = bool(rule.get("ports")) and all("port" in p for p in rule["ports"])
                    if not (key == "ingress" and app and fixed and ports <= world_ingress):
                        found.append(f"{where}: no {peers_key}, so any peer")
                for peer in peers:
                    if peer.get("podSelector") == {} and "namespaceSelector" not in peer:
                        found.append(f"{where}: podSelector {{}} admits every pod in the namespace")
                    if peer.get("namespaceSelector") == {}:
                        found.append(f"{where}: namespaceSelector {{}} admits every namespace")
                    block = peer.get("ipBlock")
                    if block:
                        net = ipaddress.ip_network(block["cidr"], strict=False)
                        meta = ipaddress.ip_address(METADATA if net.version == 4 else METADATA_V6)
                        if meta in net and _in_block(str(meta), block):
                            found.append(f"{where}: ipBlock {block['cidr']} reaches cloud metadata")
    return found


def check(path: str, *, world_ingress: frozenset[int] = frozenset(), pod_identity: bool = False,
          show: bool = False) -> list[str]:
    return check_docs(load(path), world_ingress=world_ingress, pod_identity=pod_identity, show=show)


def check_docs(docs: list[dict[str, Any]], *, world_ingress: frozenset[int] = frozenset(),
               pod_identity: bool = False, show: bool = False) -> list[str]:
    w = world(docs)
    problems = default_deny_gaps(w)
    problems += widening(w.policies, world_ingress)
    problems += [f"unexpected role {p.role} for {p.name}" for p in w.pods if p.role.startswith("unknown:")]
    actual = paths(w)
    want = expected(w, world_ingress_ports=world_ingress, pod_identity=pod_identity, broker_kv=broker_kv_store(docs))
    problems += [f"allowed but not expected: {s} -> {d}:{p}/{pr}" for s, d, p, pr in sorted(actual - want)]
    problems += [f"expected but blocked: {s} -> {d}:{p}/{pr}" for s, d, p, pr in sorted(want - actual)]
    if show:
        for row in table(w, actual):
            print(row)
    return problems


def table(w: World, actual: set[Path]) -> list[str]:
    """One line per source and destination: the ports that are open."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for src, dst, port, proto in actual:
        grouped.setdefault((src, dst), []).append(f"{port}/{proto}")
    return [f"{s} -> {d}: {', '.join(sorted(v, key=lambda x: (int(x.split('/')[0]), x)))}"
            for (s, d), v in sorted(grouped.items())]


if __name__ == "__main__":
    args = sys.argv[1:]
    target = args[0]
    ports = frozenset(int(a) for a in args[args.index("--allow-world-ingress") + 1:] if a.isdigit()) \
        if "--allow-world-ingress" in args else frozenset()
    issues = check(target, world_ingress=ports, pod_identity="--pod-identity" in args, show="--print" in args)
    for issue in issues:
        print(f"!! {issue}")
    sys.exit(1 if issues else 0)

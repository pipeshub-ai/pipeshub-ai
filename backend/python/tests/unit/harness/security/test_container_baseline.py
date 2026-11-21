"""The container baseline every shipped service keeps (ADR-OPS-02, SEC-D-02 and SEC-D-10).

The proofs in ``test_sec_d_ops_supply_chain.py`` check the two findings as the auditor stated them. This file is the
standing rule behind them: a new Compose service, a new overlay or a new Helm container that leaves the baseline fails
here, with the service and the rule it broke. It reads the files as data (Helm is rendered when ``helm`` is installed),
so it needs no Docker.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[6]
COMPOSE_DIR = REPO / "deployment" / "docker-compose"
E2E_DIR = REPO / "deployment" / "e2e"
CHART = REPO / "deployment" / "helm" / "pipeshub-ai"

# The files an install runs: the base file and the overlays its docs name. The other Compose files under
# deployment/docker-compose are classified below, so a new file must be placed in one list or the other.
SHIPPED = ("docker-compose.yml", "docker-compose.monitoring.yml", "docker-compose.agent-harness.yml",
           "docker-compose.egress-tunnels.yml", "docker-compose.secret-key-file.yml",
           # Main's opt-in PostgreSQL document store (#5274): install.sh runs it from PIPESHUB_COMPOSE_OVERRIDES.
           "docker-compose.postgres-docstore.yml")
# Contributor builds, legacy layouts and integration fixtures: never what install.sh or the docs start for an
# install. docker-compose.sandbox-legacy.yml belongs to the legacy Docker proxy (R38-EGRESS). They may not weaken a
# service the base file hardens (test_no_compose_file_opens_a_hole), but they are not held to the full baseline.
NOT_SHIPPED = ("docker-compose.build.neo4j.yml", "docker-compose.prod-legacy.yml", "docker-compose.sandbox-legacy.yml",
               "docker-compose.integration.arango.yml", "docker-compose.integration.neo4j.yml",
               "docker-compose.integration.bookstack-sim.yml", "docker-compose.integration.graph-db.yml",
               "docker-compose.integration.kv.yml", "docker-compose.integration.messaging.yml",
               "docker-compose.integration.redis-cluster.yml", "docker-compose.integration.salesforce-sim.yml",
               "docker-compose.integration.servicenow-sim.yml", "docker-compose.integration.sharepoint-sim.yml",
               "docker-compose.integration.vector-db.yml", "docker-compose.integration.zammad-sim.yml")

# Each capability a service adds back, and only those. The reason is beside each in docker-compose.yml; widening
# one means changing this table in the same review.
CAP_ADD = {
    # KILL (R43SOCK-FU-3): the init stays root as PID 1 while the service runs as another user; see stop_signal_gaps.
    "pipeshub-ai": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "SETPCAP", "KILL"},
    "mongodb": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"},
    # The stock PostgreSQL entrypoint owns its directories for the postgres user, then drops to it with gosu.
    "postgres": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"},
    "neo4j": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "KILL"},
    "redis": {"CHOWN", "DAC_READ_SEARCH", "SETUID", "SETGID"},
    # ADR-OPS-03: hands each etcd client its own certificate files; no network, read-only root.
    "etcd-tls": {"CHOWN"},
    # R51-COMPAT (ADR-OPS-07): started as root when DOCKER_GID is unset, it takes the socket's group and drops to uid
    # 1000 before serving (docker_proxy.drop_root); a start as 1000 never holds them.
    "docker-socket-proxy": {"SETUID", "SETGID"},
}
# The sandbox-facing services, and the others whose image writes only to their volumes.
READ_ONLY = {"egress-proxy", "sandbox-broker", "sandbox-image", "redis", "etcd", "etcd-tls", "web-readers-gateway",
             "docker-socket-proxy"}
# R38IDENT-FU-1: the Docker API policy proxy, the only socket holder, is part of the sandbox plane too.
SANDBOX_PLANE = ("egress-proxy", "sandbox-broker", "docker-socket-proxy")


class _Reset:
    pass


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!reset", lambda loader, node: _Reset())
_Loader.add_constructor("!override", lambda loader, node: (
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode) else loader.construct_scalar(node)))


def _load(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}


def _services(path: Path) -> dict[str, dict[str, Any]]:
    return {name: svc or {} for name, svc in (_load(path).get("services") or {}).items()}


def _limits(svc: dict[str, Any]) -> dict[str, Any]:
    return ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}


def _opts(svc: dict[str, Any]) -> list[str]:
    return [str(o).replace("=", ":").replace(" ", "").lower() for o in svc.get("security_opt") or []]


def holes(svc: dict[str, Any]) -> list[str]:
    """What in one service definition gives a container more than the baseline allows, wherever it appears."""
    found = []
    if svc.get("privileged") is True:
        found.append("privileged")
    found.extend(
        f"{key}: host"
        for key in ("network_mode", "pid", "ipc", "userns_mode", "uts", "cgroup")
        if str(svc.get(key) or "").lower() == "host"
    )
    found.extend(
        f"security_opt {opt}"
        for opt in _opts(svc)
        if opt in {"seccomp:unconfined", "apparmor:unconfined", "label:disable", "systempaths:unconfined",
                   "no-new-privileges:false"}
    )
    if any(str(c).upper().removeprefix("CAP_") == "ALL" for c in svc.get("cap_add") or []):
        found.append("cap_add ALL")
    if "cap_drop" in svc and not isinstance(svc["cap_drop"], _Reset) \
            and [str(c).upper() for c in svc["cap_drop"] or []] != ["ALL"]:
        found.append("cap_drop narrowed")
    found.extend(f"{key} reset" for key in ("cap_drop", "security_opt", "read_only", "user") if isinstance(svc.get(key), _Reset))
    if svc.get("read_only") is False:
        found.append("read_only: false")
    found.extend(f"device {vol}" for vol in svc.get("devices") or [])
    return found


def is_root(user: object) -> bool:
    name = str(user or "").strip().split(":", 1)[0].lower()
    return name in {"", "0", "root"} or (name.isdigit() and int(name) == 0)


# Images whose own ENTRYPOINT is an init that stays PID 1 as the user the container starts as. Checked against the
# images on Docker by test_the_image_init_table_matches_the_images.
IMAGE_INITS = {"neo4j": "tini -g"}
INIT_BINARIES = frozenset({"tini", "dumb-init", "docker-init", "catatonit", "s6-svscan"})


def _cap_set(svc: dict[str, Any]) -> set[str]:
    return {str(c).upper().removeprefix("CAP_") for c in svc.get("cap_add") or []}


def stop_signal_gaps(svc: dict[str, Any]) -> list[str]:
    """Whether ``docker stop`` reaches the server (R43SOCK-FU-3, ADR-OPS-02 Amendment R44-STOP).

    Docker signals PID 1. When PID 1 is an init that started as root and the server dropped to another user (which,
    with every capability dropped, only SETUID allows), the init needs KILL to pass the signal on: a root process
    holds no special right to signal another user without it, so tini gave up and the container exited 1 at once,
    with no drain and no clean shutdown. KILL is held for nothing else."""
    caps = _cap_set(svc)
    entrypoint_kept = "entrypoint" not in svc
    init = svc.get("init") is True or (entrypoint_kept and _image_repo(_image_ref(svc)) in IMAGE_INITS)
    root_init = init and is_root(svc.get("user"))
    if root_init and "SETUID" in caps and "KILL" not in caps:
        return ["its init stays root and cannot signal the user the service drops to (add KILL)"]
    if "KILL" in caps and not (root_init and "SETUID" in caps):
        return ["KILL without a root init that signals another user"]
    return []


def baseline_gaps(name: str, svc: dict[str, Any]) -> list[str]:
    """What a shipped service is missing of the baseline."""
    gaps = holes(svc) + stop_signal_gaps(svc)
    if [str(c).upper() for c in svc.get("cap_drop") or []] != ["ALL"]:
        gaps.append("cap_drop is not [ALL]")
    if "no-new-privileges:true" not in _opts(svc):
        gaps.append("no no-new-privileges")
    if not (svc.get("pids_limit") or _limits(svc).get("pids")):
        gaps.append("no pids limit")
    if not (svc.get("mem_limit") or _limits(svc).get("memory")):
        gaps.append("no memory limit")
    extra = {str(c).upper().removeprefix("CAP_") for c in svc.get("cap_add") or []} - CAP_ADD.get(name, set())
    if extra:
        gaps.append(f"cap_add beyond its table: {sorted(extra)}")
    if name in READ_ONLY and svc.get("read_only") is not True:
        gaps.append("root filesystem is writable")
    if name in SANDBOX_PLANE and is_root(svc.get("user")):
        gaps.append("runs as root")
    return gaps


def test_every_compose_file_is_classified() -> None:
    on_disk = {p.name for p in COMPOSE_DIR.glob("docker-compose*.yml")}
    assert on_disk == set(SHIPPED) | set(NOT_SHIPPED), "classify the new Compose file in SHIPPED or NOT_SHIPPED"


def test_every_shipped_service_keeps_the_baseline() -> None:
    gaps = {}
    for path in (COMPOSE_DIR / "docker-compose.yml", COMPOSE_DIR / "docker-compose.monitoring.yml"):
        for name, svc in _services(path).items():
            if path.name != "docker-compose.yml" and name in _services(COMPOSE_DIR / "docker-compose.yml"):
                continue
            if found := baseline_gaps(name, svc):
                gaps[f"{path.name}:{name}"] = found
    assert gaps == {}


def test_the_rendered_base_file_keeps_the_baseline() -> None:
    """The anchors merge as Compose merges them (``docker compose config``), not only as YAML reads them."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "SECRET_KEY": "x" * 48, "GRAFANA_ADMIN_PASSWORD": "x",
           "HOME": "/tmp"}
    out = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.monitoring.yml", "--profile", "*",
         "config", "--no-interpolate", "--format", "yaml"],
        cwd=COMPOSE_DIR, env=env, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {out.stderr.strip()[:200]}")
    rendered = yaml.safe_load(out.stdout)["services"]
    gaps = {n: g for n, s in rendered.items() if (g := baseline_gaps(n, s))}
    assert gaps == {}


@pytest.mark.parametrize("name", sorted(set(SHIPPED) | set(NOT_SHIPPED)))
def test_no_compose_file_opens_a_hole(name: str) -> None:
    found = {svc: h for svc, s in _services(COMPOSE_DIR / name).items() if (h := holes(s))}
    assert found == {}


@pytest.mark.parametrize("path", sorted(p.name for p in E2E_DIR.glob("docker-compose*.yml")))
def test_lane_overlays_do_not_weaken_the_sandbox_plane(path: str) -> None:
    """The lane stacks prove the shape an install runs, so no overlay may loosen the proxy or the broker."""
    found = {}
    for name, svc in _services(E2E_DIR / path).items():
        if name in SANDBOX_PLANE:
            if h := holes(svc):
                found[name] = h
            if "user" in svc and is_root(svc["user"]):
                found.setdefault(name, []).append("runs as root")
            if "group_add" in svc:
                found.setdefault(name, []).append("group_add replaces DOCKER_GID")
    assert found == {}


def test_install_and_the_lane_give_the_socket_proxy_the_sockets_group() -> None:
    install = (COMPOSE_DIR / "install.sh").read_text(encoding="utf-8")
    # Read fresh on every run, by the wizard and by an upgrade (R51-COMPAT: an upstream .env had none).
    assert re.search(r"^docker_socket_gid\(\) \{\n(?:.*\n){0,3}?\s+stat -c %g /var/run/docker\.sock$", install, re.M)
    assert install.count('DOCKER_GID="$(docker_socket_gid)"') == 1 and '_docker_gid="$(docker_socket_gid)"' in install
    assert re.search(r"^DOCKER_GID=\$\{DOCKER_GID\}$", install, re.M), "install.sh does not write DOCKER_GID to .env"
    lane = (E2E_DIR / "run.sh").read_text(encoding="utf-8")
    assert re.search(r"^DOCKER_GID=\$\(stat -c %g /var/run/docker\.sock\)$", lane, re.M)
    services = _services(COMPOSE_DIR / "docker-compose.yml")
    assert services["docker-socket-proxy"].get("group_add") == ["${DOCKER_GID:-0}"]
    # The broker holds neither the socket nor its group (R38IDENT-FU-1).
    assert "group_add" not in services["sandbox-broker"]
    assert not [v for v in services["sandbox-broker"].get("volumes") or [] if "docker.sock" in str(v)]


# --- what an attacker would try next: each shape must be caught --------------------------------------------------

_BASE = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "read_only": True, "user": "1000:1000",
         "deploy": {"resources": {"limits": {"memory": "512M", "pids": 4096}}}}


@pytest.mark.parametrize("patch, expect", [
    ({"user": "0:0"}, "runs as root"),
    ({"user": "root"}, "runs as root"),
    ({"user": "0"}, "runs as root"),
    ({"user": "00:1000"}, "runs as root"),
    ({"user": "ROOT:root"}, "runs as root"),
    ({"user": ""}, "runs as root"),
    ({"cap_drop": ["NET_RAW"]}, "cap_drop is not [ALL]"),
    ({"cap_drop": []}, "cap_drop is not [ALL]"),
    ({"cap_add": ["ALL"]}, "cap_add ALL"),
    ({"cap_add": ["cap_all"]}, "cap_add ALL"),
    ({"cap_add": ["SYS_ADMIN"]}, "cap_add beyond its table: ['SYS_ADMIN']"),
    ({"cap_add": ["CAP_NET_RAW"]}, "cap_add beyond its table: ['NET_RAW']"),
    ({"security_opt": ["no-new-privileges:true", "seccomp:unconfined"]}, "security_opt seccomp:unconfined"),
    ({"security_opt": ["no-new-privileges:true", "seccomp=unconfined"]}, "security_opt seccomp:unconfined"),
    ({"security_opt": ["no-new-privileges:true", "Seccomp=Unconfined"]}, "security_opt seccomp:unconfined"),
    ({"security_opt": ["no-new-privileges:true", "apparmor=unconfined"]}, "security_opt apparmor:unconfined"),
    ({"security_opt": ["no-new-privileges:true", "label=disable"]}, "security_opt label:disable"),
    ({"security_opt": ["no-new-privileges=false"]}, "no no-new-privileges"),
    ({"security_opt": []}, "no no-new-privileges"),
    ({"privileged": True}, "privileged"),
    ({"network_mode": "host"}, "network_mode: host"),
    ({"pid": "host"}, "pid: host"),
    ({"ipc": "HOST"}, "ipc: host"),
    ({"userns_mode": "host"}, "userns_mode: host"),
    ({"read_only": False}, "read_only: false"),
    ({"devices": ["/dev/kmsg"]}, "device /dev/kmsg"),
    ({"deploy": {}}, "no pids limit"),
])
def test_a_weakened_sandbox_plane_service_is_caught(patch: dict[str, Any], expect: str) -> None:
    assert baseline_gaps("egress-proxy", _BASE) == []
    assert expect in baseline_gaps("egress-proxy", {**_BASE, **patch})


def test_a_reset_in_an_overlay_is_caught() -> None:
    svc = _load_text("services:\n  egress-proxy:\n    cap_drop: !reset []\n    read_only: !reset null\n")
    assert {"cap_drop reset", "read_only reset"} <= set(holes(svc["services"]["egress-proxy"]))


def _load_text(text: str) -> dict[str, Any]:
    return yaml.load(text, Loader=_Loader)


# --- Helm ---------------------------------------------------------------------------------------------------------

# The hardened profile (ADR-OPS-07): the baseline is what a hardened release renders; the compatibility one keeps the
# previous release's pods (deployment/helm/tests/test_security_profile.py).
_SECRETS = ["--set", "hardened=true", "--set", "secretKey=ci-secret-key-0123456789abcdef0123456789abcdef", "--set", "mongodb.auth.rootPassword=r",
            "--set", "redis.auth.password=r", "--set", "neo4j.auth.password=r", "--set", "qdrant.apiKey=k",
            "--set", "mongodb.auth.usernames[0]=p", "--set", "mongodb.auth.passwords[0]=p",
            "--set", "mongodb.auth.databases[0]=p"]
_VARIANTS = {
    "broker-dind": ["--set", "sandbox.dind.enabled=true", "--set", "persistence.accessModes={ReadWriteMany}"],
    "builtin-stores": ["-f", "values-local.yaml", "--set", "mongodb.enabled=false", "--set", "mongodb.builtin.enabled=true",
                       "--set", "redis.enabled=false", "--set", "redis.builtin.enabled=true", "--set", "arango.enabled=true",
                       "--set", "arango.auth.rootPassword=r", "--set", "etcd.enabled=true"],
}
# The one documented exception: Docker-in-Docker needs a privileged container (values.yaml sandbox.dind).
_HELM_PRIVILEGED = {"dind"}
# The two the auditor named, plus the subcharts' own: read-only roots.
_HELM_READ_ONLY = {"pipeshub-ai", "sandbox-broker", "model-cache"}


def _pods(rendered: str) -> list[tuple[str, dict[str, Any]]]:
    pods = []
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") not in {"Deployment", "StatefulSet", "Job", "DaemonSet"}:
            continue
        pods.append((f"{doc['kind']}/{doc['metadata']['name']}", doc["spec"]["template"]["spec"]))
    return pods


def helm_gaps(pods: list[tuple[str, dict[str, Any]]]) -> dict[str, list[str]]:
    gaps: dict[str, list[str]] = {}
    for where, spec in pods:
        pod_sc = spec.get("securityContext") or {}
        if spec.get("automountServiceAccountToken") is not False:
            gaps.setdefault(where, []).append("mounts a service-account token")
        for key in ("hostNetwork", "hostPID", "hostIPC"):
            if spec.get(key):
                gaps.setdefault(where, []).append(key)
        for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
            sc = c.get("securityContext") or {}
            at = f"{where}:{c['name']}"
            if c["name"] in _HELM_PRIVILEGED:
                continue
            if sc.get("privileged"):
                gaps.setdefault(at, []).append("privileged")
            if sc.get("allowPrivilegeEscalation") is not False:
                gaps.setdefault(at, []).append("may escalate")
            if "ALL" not in ((sc.get("capabilities") or {}).get("drop") or []):
                gaps.setdefault(at, []).append("keeps capabilities")
            if (sc.get("capabilities") or {}).get("add"):
                gaps.setdefault(at, []).append("adds capabilities")
            if not (sc.get("runAsNonRoot") or pod_sc.get("runAsNonRoot")) or 0 in (sc.get("runAsUser"),
                                                                                  pod_sc.get("runAsUser")):
                gaps.setdefault(at, []).append("may run as root")
            seccomp = (sc.get("seccompProfile") or pod_sc.get("seccompProfile") or {}).get("type")
            if seccomp not in {"RuntimeDefault", "Localhost"}:
                gaps.setdefault(at, []).append("no seccomp profile")
            if c["name"] in _HELM_READ_ONLY and sc.get("readOnlyRootFilesystem") is not True:
                gaps.setdefault(at, []).append("writable root")
    return gaps


@pytest.mark.parametrize("variant", sorted(_VARIANTS))
def test_every_rendered_helm_container_keeps_the_baseline(variant: str) -> None:
    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
    if not (CHART / "charts").is_dir():
        pytest.skip("chart dependencies not built (helm dependency build)")
    out = subprocess.run(["helm", "template", "ci", ".", *_SECRETS, *_VARIANTS[variant]],
                         cwd=CHART, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert helm_gaps(_pods(out.stdout)) == {}


def test_a_weakened_helm_container_is_caught() -> None:
    good = {"automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": "pipeshub-ai", "securityContext": {
                "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}}]}
    assert helm_gaps([("Deployment/app", good)]) == {}
    import copy

    for mutate, expect in [
        (lambda s: s.pop("automountServiceAccountToken"), "mounts a service-account token"),
        (lambda s: s.update(hostNetwork=True), "hostNetwork"),
        (lambda s: s["containers"][0]["securityContext"].update(privileged=True), "privileged"),
        (lambda s: s["containers"][0]["securityContext"].update(allowPrivilegeEscalation=True), "may escalate"),
        (lambda s: s["containers"][0]["securityContext"].update(capabilities={"drop": ["NET_RAW"]}), "keeps capabilities"),
        (lambda s: s["containers"][0]["securityContext"]["capabilities"].update(add=["NET_ADMIN"]), "adds capabilities"),
        (lambda s: s["containers"][0]["securityContext"].update(runAsUser=0), "may run as root"),
        (lambda s: s["securityContext"].pop("seccompProfile"), "no seccomp profile"),
        (lambda s: s["securityContext"].update(seccompProfile={"type": "Unconfined"}), "no seccomp profile"),
        (lambda s: s["containers"][0]["securityContext"].update(readOnlyRootFilesystem=False), "writable root"),
    ]:
        spec = copy.deepcopy(good)
        mutate(spec)
        assert expect in sum(helm_gaps([("Deployment/app", spec)]).values(), []), expect


# --- the contributor, legacy and integration files (R38IDENT-FU-2) -----------------------------------------------

# Compose files one directory down: the beta and hot-reload layouts, and the test-only overlays the shell suites
# layer on an install. Each is classified like the files above, and none may open a hole.
SUBDIR_FILES = ("beta/docker-compose.build.neo4j.slim.yml", "beta/docker-compose.build.opensearch.neo4j.yml",
                "hot-reload/docker-compose.dev.arango.yml", "hot-reload/docker-compose.dev.neo4j.yml",
                "tests/docker-compose.agent-harness.test.yml", "tests/docker-compose.airgap-install.yml",
                "tests/docker-compose.sandbox-smoke.yml")
# The files whose services that run an image the base file runs keep the base file's baseline.
# docker-compose.sandbox-legacy.yml is held by test_every_shipped_service_keeps_the_baseline's rules once its proxy
# moves off root; the tests/ overlays only layer onto the base file.
BASELINE_FILES = ("docker-compose.build.neo4j.yml", "docker-compose.prod-legacy.yml",
                  *(n for n in NOT_SHIPPED if n.startswith("docker-compose.integration.")), *SUBDIR_FILES[:4])
# Each image repository the base file runs, and the base-file service whose capability table and read-only rule it
# takes. The app's image takes the service's own name: the app, the legacy Docker API proxy and etcd-tls run it
# with different privileges.
BASE_IMAGES = {"pipeshubai/pipeshub-ai": None, "pipeshub-ai": None, "mongo": "mongodb", "redis": "redis",
               "neo4j": "neo4j", "arangodb": "arango", "quay.io/coreos/etcd": "etcd",
               "confluentinc/cp-zookeeper": "zookeeper", "confluentinc/cp-kafka": "kafka-1", "qdrant/qdrant": "qdrant",
               "pipeshub/sandbox": "sandbox-image", "pipeshubai/pipeshub-sandbox": "sandbox-image"}
# Redis 8's entrypoint empties its bounding set before it drops to the redis user, and stops without SETPCAP
# (found by starting redis:8.4 with the base file's set).
IMAGE_CAP_ADD = {"redis:8": CAP_ADD["redis"] | {"SETPCAP"}}
# The legacy Docker API policy proxy: the socket's group, never root, and a read-only root.
LEGACY_PROXY = "docker-socket-proxy"


def _image_ref(svc: dict[str, Any]) -> str:
    """The image a service runs, with each ``${VAR:-default}`` read as its default."""
    ref = str(svc.get("image") or "")
    while (m := re.search(r"\$\{[A-Za-z0-9_]+:?-([^${}]*)\}", ref)):
        ref = ref[:m.start()] + m.group(1) + ref[m.end():]
    return ref.split("@", 1)[0]


def _image_repo(ref: str) -> str:
    head, _, tail = ref.rpartition(":")
    return head if head and "/" not in tail else ref


def _resolved(path: Path, svc: dict[str, Any]) -> dict[str, Any]:
    """The service as ``extends`` makes it: the extended service's keys, then the file's own."""
    ext = svc.get("extends")
    if not isinstance(ext, dict):
        return svc
    base = _resolved(path.parent / ext["file"], _services(path.parent / ext["file"])[ext["service"]])
    merged = {**base, **svc}
    return {k: v for k, v in merged.items() if k != "extends" and not isinstance(v, _Reset)}


def same_image_as_the_base(path: Path, name: str, svc: dict[str, Any]) -> str | None:
    """The base-file service whose rules this one keeps, or None when it runs an image the base file does not."""
    build = svc.get("build")
    if isinstance(build, dict) and build.get("dockerfile", "Dockerfile") != "Dockerfile":
        return None
    repo = _image_repo(_image_ref(svc))
    if repo not in BASE_IMAGES:
        return None
    return BASE_IMAGES[repo] or name


def contributor_gaps(path: Path, name: str, svc: dict[str, Any]) -> list[str]:
    canonical = same_image_as_the_base(path, name, svc)
    if canonical is None:
        return []
    ref = _image_ref(svc)
    allowed = next((caps for prefix, caps in IMAGE_CAP_ADD.items() if ref.startswith(prefix)),
                   CAP_ADD.get(canonical, set()))
    gaps = [g for g in baseline_gaps(canonical, svc) if not g.startswith("cap_add beyond")]
    extra = {str(c).upper().removeprefix("CAP_") for c in svc.get("cap_add") or []} - allowed
    if extra:
        gaps.append(f"cap_add beyond its table: {sorted(extra)}")
    if name == LEGACY_PROXY:
        if is_root(svc.get("user")):
            gaps.append("runs as root")
        if svc.get("read_only") is not True:
            gaps.append("root filesystem is writable")
        if svc.get("group_add") != ["${DOCKER_GID:-0}"]:
            gaps.append("does not reach the socket through DOCKER_GID")
    return gaps


def _same_image_services(rel: str) -> dict[str, dict[str, Any]]:
    path = COMPOSE_DIR / rel
    out = {}
    for name, declared in _services(path).items():
        if not (declared.get("image") or declared.get("extends") or declared.get("build")):
            continue
        svc = _resolved(path, declared)
        if same_image_as_the_base(path, name, svc) is not None:
            out[name] = svc
    return out


def test_every_subdirectory_compose_file_is_classified() -> None:
    on_disk = {str(p.relative_to(COMPOSE_DIR)) for p in COMPOSE_DIR.glob("*/docker-compose*.yml")}
    assert on_disk == set(SUBDIR_FILES), "classify the new Compose file in SUBDIR_FILES"


@pytest.mark.parametrize("rel", SUBDIR_FILES)
def test_no_subdirectory_compose_file_opens_a_hole(rel: str) -> None:
    found = {svc: h for svc, s in _services(COMPOSE_DIR / rel).items() if (h := holes(s))}
    assert found == {}


@pytest.mark.parametrize("rel", BASELINE_FILES)
def test_every_same_image_service_keeps_the_baseline(rel: str) -> None:
    """A store or app service in a contributor file runs the image the base file hardened, so it keeps that shape."""
    path = COMPOSE_DIR / rel
    gaps = {n: g for n, s in _same_image_services(rel).items() if (g := contributor_gaps(path, n, s))}
    assert gaps == {}


@pytest.mark.parametrize("rel", BASELINE_FILES)
def test_every_rendered_same_image_service_keeps_the_baseline(rel: str) -> None:
    """The anchors and ``extends`` merge as Compose merges them, not only as this file reads them."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    out = subprocess.run(["docker", "compose", "-f", rel, "--profile", "*", "config", "--no-interpolate",
                          "--format", "yaml"], cwd=COMPOSE_DIR, env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                                                                     "HOME": "/tmp"},
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {out.stderr.strip()[:200]}")
    path = COMPOSE_DIR / rel
    rendered = yaml.safe_load(out.stdout)["services"]
    expected = set(_same_image_services(rel))
    gaps = {n: g for n, s in rendered.items() if n in expected and (g := contributor_gaps(path, n, s))}
    assert gaps == {}


_SIZE_UNITS = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}


def _bytes(value: object) -> int | None:
    """A Compose or Neo4j size (``4G``, ``512m``, ``${VAR:-2G}``) in bytes, read at its default."""
    m = re.fullmatch(r"(?:\$\{[A-Za-z0-9_]+:?-)?(\d+)([kKmMgG]?)[bB]?\}?", str(value).strip())
    return int(m.group(1)) * _SIZE_UNITS[m.group(2).lower()] if m else None


def _neo4j_setting(env: dict[str, str], name: str) -> str | None:
    # The image maps NEO4J_server_memory_* (5.x) and the deprecated NEO4J_dbms_memory_* to the same setting.
    return env.get(f"NEO4J_server_memory_{name}") or env.get(f"NEO4J_dbms_memory_{name}")


def neo4j_memory_gaps(svc: dict[str, Any]) -> list[str]:
    """A Neo4j under a memory limit that leaves its heap or page cache unset sizes them from the host's memory, finds
    they exceed the limit, and exits 3 at start; so both are set, and fit under the limit together."""
    if _image_repo(_image_ref(svc)) != "neo4j":
        return []
    limit = _bytes(_limits(svc).get("memory"))
    if limit is None:
        return []
    raw = svc.get("environment") or {}
    env = dict(str(e).split("=", 1) for e in raw if "=" in str(e)) if isinstance(raw, list) else dict(raw)
    heap, cache = _neo4j_setting(env, "heap_max__size"), _neo4j_setting(env, "pagecache_size")
    if heap is None or cache is None:
        return ["memory limit without a heap and page cache size"]
    if (_bytes(heap) or 0) + (_bytes(cache) or 0) >= limit:
        return ["heap and page cache do not fit under the memory limit"]
    return []


@pytest.mark.parametrize("rel", sorted(str(p.relative_to(COMPOSE_DIR))
                                       for p in COMPOSE_DIR.rglob("docker-compose*.yml")))
def test_every_limited_neo4j_sizes_its_memory_within_the_limit(rel: str) -> None:
    gaps = {n: g for n, s in _services(COMPOSE_DIR / rel).items() if (g := neo4j_memory_gaps(s))}
    assert gaps == {}


@pytest.mark.parametrize(("env", "limit", "expect"), [
    ({"NEO4J_AUTH": "neo4j/x"}, "4G", ["memory limit without a heap and page cache size"]),
    ({"NEO4J_server_memory_heap_max__size": "2G", "NEO4J_server_memory_pagecache_size": "1G"}, "4G", []),
    (["NEO4J_dbms_memory_heap_max__size=${NEO4J_HEAP_MAX:-2G}", "NEO4J_dbms_memory_pagecache_size=1G"], "4G", []),
    ({"NEO4J_server_memory_heap_max__size": "3G", "NEO4J_server_memory_pagecache_size": "1G"}, "4G",
     ["heap and page cache do not fit under the memory limit"]),
    ({"NEO4J_AUTH": "neo4j/x"}, None, []),
])
def test_the_neo4j_memory_rule_sees_each_shape(env: object, limit: str | None, expect: list[str]) -> None:
    svc: dict[str, Any] = {"image": "neo4j:5.26.0@sha256:abc", "environment": env}
    if limit:
        svc["deploy"] = {"resources": {"limits": {"memory": limit}}}
    assert neo4j_memory_gaps(svc) == expect


def test_the_same_image_rule_sees_each_shape() -> None:
    assert _image_ref({"image": "${SANDBOX_DOCKER_IMAGE:-pipeshubai/pipeshub-sandbox:${IMAGE_TAG:-latest}}"}) \
        == "pipeshubai/pipeshub-sandbox:latest"
    assert _image_repo("quay.io/coreos/etcd:v3.5.17") == "quay.io/coreos/etcd"
    assert _image_repo("localhost:5000/redis") == "localhost:5000/redis"
    here = COMPOSE_DIR / "x.yml"
    assert same_image_as_the_base(here, "redis-x", {"image": "redis:7-alpine@sha256:ab"}) == "redis"
    assert same_image_as_the_base(here, "pipeshub-ai", {"image": "pipeshub-ai:latest",
                                                        "build": {"dockerfile": "Dockerfile.dev"}}) is None
    assert same_image_as_the_base(here, "opensearch", {"image": "opensearchproject/opensearch:3.6.0"}) is None
    hardened = {**_BASE, "user": None, "image": "redis:8.4", "cap_add": ["SETUID", "SETGID", "SETPCAP"]}
    assert contributor_gaps(here, "redis-it", hardened) == []
    assert "cap_add beyond its table: ['SETPCAP']" in contributor_gaps(here, "redis-it", {**hardened,
                                                                                        "image": "redis:7.4.12"})
    assert "root filesystem is writable" in contributor_gaps(here, "redis-it", {**hardened, "read_only": None})
    proxy = {**_BASE, "image": "pipeshub-ai:latest", "group_add": ["${DOCKER_GID:-0}"]}
    assert contributor_gaps(here, LEGACY_PROXY, proxy) == []
    assert "runs as root" in contributor_gaps(here, LEGACY_PROXY, {**proxy, "user": "0:0"})
    assert "does not reach the socket through DOCKER_GID" in contributor_gaps(here, LEGACY_PROXY,
                                                                               {**proxy, "group_add": None})
    assert "no pids limit" in contributor_gaps(here, "kafka-1", {**_BASE, "image": "confluentinc/cp-kafka:7.9.0",
                                                                  "deploy": {}})


# --- the stop signal reaches the server (R43SOCK-FU-3, ADR-OPS-02 Amendment R44-STOP) -----------------------------

# Images whose default command, as PID 1, exits once it has passed SIGTERM on, so the server is killed mid-shutdown
# with it; the base file runs the server itself under the init.
IMAGE_EARLY_EXIT = {"qdrant/qdrant": ["./qdrant"]}


def test_the_stop_signal_rule_sees_each_shape() -> None:
    app = {"image": "pipeshub-ai:latest", "init": True, "cap_add": ["SETUID", "SETGID", "KILL"]}
    assert stop_signal_gaps(app) == []
    assert "add KILL" in stop_signal_gaps({**app, "cap_add": ["SETUID", "SETGID"]})[0]
    assert "add KILL" in stop_signal_gaps({**app, "user": "0:0", "cap_add": ["SETUID"]})[0]
    # The image's own tini, unless the service replaces the entrypoint.
    neo4j = {"image": "neo4j:5.26.0@sha256:ab", "cap_add": ["SETUID", "SETGID"]}
    assert "add KILL" in stop_signal_gaps(neo4j)[0]
    assert stop_signal_gaps({**neo4j, "entrypoint": ["/startup/docker-entrypoint.sh"]}) == []
    # No hop to another user: same uid as the init (or no SETUID, so no drop), and no KILL is needed or allowed.
    assert stop_signal_gaps({**app, "user": "1000:1000", "cap_add": []}) == []
    assert stop_signal_gaps({"image": "redis:7", "cap_add": ["SETUID", "SETGID"]}) == []
    assert "KILL without" in stop_signal_gaps({**app, "user": "1000:1000", "cap_add": ["KILL"]})[0]
    assert "KILL without" in stop_signal_gaps({"image": "redis:7", "cap_add": ["SETUID", "KILL"]})[0]
    assert "KILL without" in stop_signal_gaps({**app, "init": False})[0]


def _early_exit_gaps(services: dict[str, dict[str, Any]]) -> dict[str, str]:
    found = {}
    for name, svc in services.items():
        command = IMAGE_EARLY_EXIT.get(_image_repo(_image_ref(svc)))
        if command is not None and (svc.get("init") is not True or svc.get("command") != command):
            found[name] = f"runs the image's command as PID 1; run {command} under init: true"
    return found


def test_no_shipped_service_runs_a_command_that_exits_before_its_server() -> None:
    assert _early_exit_gaps(_services(COMPOSE_DIR / "docker-compose.yml")) == {}
    assert _early_exit_gaps({"qdrant": {"image": "qdrant/qdrant:v1.15.5"}}) != {}


def _local_image(ref: str) -> dict[str, Any] | None:
    out = subprocess.run(["docker", "image", "inspect", ref, "--format", "{{json .Config}}"],
                         capture_output=True, text=True, check=False)
    return json.loads(out.stdout) if out.returncode == 0 else None


@pytest.mark.docker
def test_the_image_init_table_matches_the_images() -> None:
    """IMAGE_INITS names exactly the base file's images whose ENTRYPOINT is an init (those present locally)."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    checked, wrong = [], {}
    for svc in _services(COMPOSE_DIR / "docker-compose.yml").values():
        ref = _image_ref(svc)
        config = _local_image(ref) if ref else None
        if config is None:
            continue
        head = Path(str((config.get("Entrypoint") or [""])[0])).name
        checked.append(ref)
        if (head in INIT_BINARIES) != (_image_repo(ref) in IMAGE_INITS):
            wrong[ref] = head
    if not checked:
        pytest.skip("none of the base file's images is present locally")
    assert wrong == {}, wrong


_PROBE_IMAGE = "redis:7.4.12-bookworm"  # the base file's own, with setpriv; any image with a shell would do
# A child as another user that exits 0 on SIGTERM, as the app's process monitor and neo4j's server do.
_DROPPED_CHILD = ["setpriv", "--reuid=999", "--regid=999", "--clear-groups", "--",
                  "sh", "-c", "trap 'exit 0' TERM; sleep 600 & wait"]


def _stop_exit(caps: set[str]) -> tuple[int, str]:
    name = f"pxh-stop-probe-{secrets.token_hex(4)}"
    run = ["docker", "run", "-d", "--name", name, "--label", "pipeshub.harness.owner=stop-probe", "--init",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--entrypoint", _DROPPED_CHILD[0]]
    run += [arg for cap in sorted(caps) for arg in ("--cap-add", cap)]
    try:
        subprocess.run([*run, _PROBE_IMAGE, *_DROPPED_CHILD[1:]], check=True, capture_output=True, timeout=60)
        # Wait until the child has dropped and installed its trap, then stop it as Compose does.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            top = subprocess.run(["docker", "top", name, "-eo", "pid,args"], capture_output=True, text=True,
                                 check=False).stdout
            if "sleep 600" in top:
                break
            time.sleep(0.2)
        subprocess.run(["docker", "stop", "-t", "20", name], check=True, capture_output=True, timeout=60)
        code = subprocess.run(["docker", "inspect", name, "--format", "{{.State.ExitCode}}"], capture_output=True,
                              text=True, check=True).stdout.strip()
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=False)
        return int(code), logs.stdout + logs.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.mark.docker
@pytest.mark.timeout(180)
@pytest.mark.parametrize("service", ["pipeshub-ai", "neo4j"])
def test_the_base_files_capabilities_let_the_init_stop_the_service(service: str) -> None:
    """With the service's own capability set, a root init stops a child that dropped to another user cleanly; the
    same set without KILL is the failure R43SOCK-FU-3 found (tini exits 1 and the child is killed)."""
    if shutil.which("docker") is None or _local_image(_PROBE_IMAGE) is None:
        pytest.skip(f"docker or {_PROBE_IMAGE} not available")
    caps = _cap_set(_services(COMPOSE_DIR / "docker-compose.yml")[service])
    code, logs = _stop_exit(caps)
    assert (code, "forwarding signal" in logs) == (0, False), logs
    code, logs = _stop_exit(caps - {"KILL"})
    assert code == 1 and "Unexpected error when forwarding signal" in logs, logs

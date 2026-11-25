"""C-12 and D1's Compose render: the Docker socket and the egress proxy's routes, as shipped.

C-12 (EXECUTION-PLAN X3): a sandbox reaches only the proxy (P5.4 SEC-1..SEC-6, real
Docker) and the proxy reaches only the gateways. The second half is a property of
the deployment, so it is asserted on what ``docker compose config`` renders for every
profile set and overlay the repository ships: the proxy sits only on internal
networks whose other members are the gateways' host, the broker and the KV store,
and no container but the Docker API policy proxy mounts the Docker socket (X-4,
R38IDENT-FU-1): the broker reaches Docker only through it, on a network of their own;
in docker mode the app reaches the same proxy, never the socket.

R51-COMPAT (ADR-OPS-07): the sandbox profile alone starts what a release without the broker started (the
sandbox image and the Docker API proxy, the app reaching it on sandbox-control); the broker and the egress proxy
are the sandbox-broker profile.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from functools import cache
from pathlib import Path
from typing import Any, cast

import pytest

from tests.cohesion.support import REPO_ROOT

COMPOSE_DIR = REPO_ROOT / "deployment" / "docker-compose"
BASE = "docker-compose.yml"
HARNESS = "docker-compose.agent-harness.yml"
LEGACY = "docker-compose.sandbox-legacy.yml"
TUNNELS = "docker-compose.egress-tunnels.yml"
SOCKET = "/var/run/docker.sock"
PROJECT = "pxh-c12"

BROKER, PROXY, APP = "sandbox-broker", "egress-proxy", "pipeshub-ai"
CONTROL_ALIAS = "egress-proxy-control"
DOCKER_PROXY, DOCKER_CONTROL, DOCKER_NET = "docker-socket-proxy", "sandbox-control", "sandbox_docker"
PLANE, KV, EGRESS = "sandbox_plane", "sandbox_kv", "sandbox_egress"
KV_STORES = {"redis", "etcd"}
GATEWAY_ALIASES = {"model-gateway", "tool-gateway", "harness-state"}
# ADR-KEY-02 (SEC-D-03): the sandbox plane reaches the KV store as its own component, never with the root key or
# the default Redis login.
KV_ENV = {"PIPESHUB_COMPONENT", "PIPESHUB_COMPONENT_KEY", "KV_STORE_TYPE", "REDIS_HOST", "REDIS_PORT", "ETCD_URL"}
PLATFORM_KEYS = {"SECRET_KEY", "SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT", "REDIS_PASSWORD"}

# name -> (overlays after the base file, COMPOSE_PROFILES)
RENDERS: dict[str, tuple[tuple[str, ...], str]] = {
    "eval": ((), "graph-neo4j"),
    # An .env from a release without the broker (compatibility): SANDBOX_MODE unset reads docker.
    "slim": ((), "graph-neo4j,sandbox"),
    "slim-broker": ((), "graph-neo4j,sandbox,sandbox-broker"),
    "full": ((), "graph-neo4j,kv-etcd,broker-kafka,sandbox,sandbox-broker"),
    "every-profile": ((), "graph-arango,graph-neo4j,kv-etcd,broker-kafka,sandbox,sandbox-broker"),
    "agent-harness": ((HARNESS,), "graph-neo4j,kv-etcd,sandbox,sandbox-broker"),
}
# What install.sh writes beside the broker's profile.
RENDER_ENV: dict[str, tuple[str, ...]] = {
    name: ("SANDBOX_MODE=broker",) for name, (_, profiles) in RENDERS.items() if "sandbox-broker" in profiles.split(",")
}
WITH_SANDBOX = [name for name, (_, profiles) in RENDERS.items() if "sandbox" in profiles.split(",")]
WITH_BROKER = [name for name, (_, profiles) in RENDERS.items() if "sandbox-broker" in profiles.split(",")]

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(["docker", "compose", "version"], capture_output=True, check=False).returncode != 0,
    reason="needs the Docker Compose v2 CLI (config only; no daemon)",
)

Service = dict[str, Any]


# What install.sh writes (SEC-D-06, ADR-OPS-03); Compose renders without them too (R51-COMPAT).
STORE_CREDENTIALS = ("REDIS_PASSWORD=ci", "QDRANT_API_KEY=ci", "MONGO_PASSWORD=ci", "KAFKA_PASSWORD=ci")


@cache
def _render(overlays: tuple[str, ...], profiles: str, env_lines: tuple[str, ...] = ()) -> dict[str, Any]:
    files = [arg for name in (BASE, *overlays) for arg in ("-f", str(COMPOSE_DIR / name))]
    with tempfile.TemporaryDirectory(prefix="pxh-c12-") as scratch:
        # An explicit env file, so a developer's deployment/docker-compose/.env is never read.
        env_file = Path(scratch) / "render.env"
        _ = env_file.write_text("\n".join((f"COMPOSE_PROFILES={profiles}", *STORE_CREDENTIALS, *env_lines)) + "\n", encoding="utf-8")
        out = subprocess.run(
            ["docker", "compose", *files, "-p", PROJECT, "--env-file", str(env_file), "config", "--format", "json"],
            # The caller's shell (SANDBOX_MODE, IMAGE_TAG, ...) must not leak into the interpolation.
            env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    return cast("dict[str, Any]", json.loads(out))


def _named(name: str) -> dict[str, Any]:
    overlays, profiles = RENDERS[name]
    return _render(overlays, profiles, RENDER_ENV.get(name, ()))


def _services(rendered: dict[str, Any]) -> dict[str, Service]:
    return cast("dict[str, Service]", rendered["services"])


def _socket_holders(rendered: dict[str, Any]) -> set[str]:
    return {
        name
        for name, service in _services(rendered).items()
        for volume in service.get("volumes", [])
        if SOCKET in (volume.get("source"), volume.get("target"))
    }


def _networks(service: Service) -> dict[str, dict[str, Any]]:
    declared = cast("dict[str, dict[str, Any] | None]", service.get("networks") or {})
    return {name: spec or {} for name, spec in declared.items()}


def _members(rendered: dict[str, Any], network: str) -> set[str]:
    return {name for name, service in _services(rendered).items() if network in _networks(service)}


@pytest.mark.parametrize("name", list(RENDERS))
def test_c12_no_service_but_the_policy_proxy_mounts_the_docker_socket(name: str) -> None:
    rendered = _named(name)
    expected: set[str] = {DOCKER_PROXY} if name in WITH_SANDBOX else set()
    assert _socket_holders(rendered) == expected


@pytest.mark.parametrize("name", WITH_BROKER)
def test_r38ident_fu1_the_broker_reaches_docker_only_through_the_policy_proxy(name: str) -> None:
    """SEC-D-02 fix 3: a foothold in the broker holds no socket and no socket group; the proxy it reaches is on an
    internal network only the two of them are on, and holds nothing but its callers' credentials."""
    rendered = _named(name)
    services = _services(rendered)
    broker, docker_proxy = services[BROKER], services[DOCKER_PROXY]
    assert broker["environment"]["DOCKER_HOST"] == f"tcp://{DOCKER_PROXY}:2375"
    assert "group_add" not in broker
    assert broker["depends_on"][DOCKER_PROXY]["condition"] == "service_healthy"
    assert _members(rendered, DOCKER_NET) == {BROKER, DOCKER_PROXY}
    assert cast("dict[str, dict[str, Any]]", rendered["networks"])[DOCKER_NET].get("internal") is True
    # sandbox-control is the app's way in for SANDBOX_MODE=docker (R51-COMPAT).
    assert set(_networks(docker_proxy)) == {DOCKER_NET, DOCKER_CONTROL}
    assert docker_proxy["image"] == services[APP]["image"]
    assert docker_proxy["entrypoint"] == ["python", "-m", "app.docker_proxy_main"]
    # DOCKER_GID unset in the render (an .env from before it): the proxy starts as root and drops to uid 1000 with the
    # socket's group itself (drop_root, R51-COMPAT); with DOCKER_GID it starts as 1000 with that group.
    assert (docker_proxy.get("user") or "", docker_proxy["group_add"]) == ("", ["0"])
    assert set(docker_proxy["cap_add"]) == {"SETUID", "SETGID"} and docker_proxy["cap_drop"] == ["ALL"]
    assert not docker_proxy.get("ports")
    env = docker_proxy["environment"]
    assert "DOCKER_PROXY_BROKER_TOKEN" in env and "DOCKER_PROXY_BROKER_TOKEN" in broker["environment"]
    assert not env.keys() & (PLATFORM_KEYS | {"PIPESHUB_COMPONENT_KEY"})
    # On the broker install.sh writes no app token, so the proxy admits the app to nothing.
    assert env["DOCKER_PROXY_TOKEN"] == services[APP]["environment"]["DOCKER_PROXY_TOKEN"] == ""
    _the_proxy_and_the_broker_share_a_scope(services)


# What BrokerScope reads: the proxy holds the broker to this install's objects and pools only if both read one value.
BROKER_SCOPE = ("SANDBOX_INSTALL_ID", "SANDBOX_DATA_VOLUME", "SANDBOX_EGRESS_NETWORK", "SANDBOX_EGRESS_PROXY_CONTAINER",
                "SANDBOX_EGRESS_SUBNETS", "SANDBOX_OPEN_EGRESS_SUBNETS",
                # R44STOP-FU-3: the image the broker runs the egress firewall from, the only one the proxy lets run it.
                "SANDBOX_DOCKER_IMAGE")


def _the_proxy_and_the_broker_share_a_scope(services: dict[str, Service]) -> None:
    proxy_env, broker_env = services[DOCKER_PROXY]["environment"], services[BROKER]["environment"]
    for scoped in BROKER_SCOPE:
        assert proxy_env[scoped] == broker_env[scoped], scoped


def test_cr1_eval_starts_no_broker_or_proxy() -> None:
    services = _services(_named("eval"))
    assert BROKER not in services
    assert PROXY not in services


def test_r51compat_the_sandbox_profile_alone_starts_what_a_release_without_the_broker_started() -> None:
    """An .env whose COMPOSE_PROFILES says sandbox and that names no SANDBOX_MODE (a release without the broker)
    starts the sandbox image and the Docker API proxy, never the broker or the egress proxy, and the app creates
    sandboxes through the proxy as it did."""
    services = _services(_named("slim"))
    assert {BROKER, PROXY} & services.keys() == set()
    assert {DOCKER_PROXY, "sandbox-image"} <= services.keys()
    assert services[APP]["environment"]["SANDBOX_MODE"] == "docker"


@pytest.mark.parametrize("name", WITH_BROKER)
def test_cr2_broker_and_proxy_run_from_the_app_image_unpublished_with_healthchecks(name: str) -> None:
    services = _services(_named(name))
    app, broker, proxy = services[APP], services[BROKER], services[PROXY]
    for service, module, host, port in (
        (broker, "app.sandbox_broker_main", "localhost", 8095),
        (proxy, "app.egress_proxy_main", CONTROL_ALIAS, 8098),
    ):
        assert service["image"] == app["image"]
        assert service["entrypoint"] == ["python", "-m", module]
        assert not service.get("ports")
        assert f"{host}:{port}/healthz" in " ".join(service["healthcheck"]["test"])
        assert service["environment"].keys() >= KV_ENV
        assert not service["environment"].keys() & PLATFORM_KEYS
    assert app["environment"]["SANDBOX_MODE"] == "broker"
    env = broker["environment"]
    assert env["SANDBOX_EGRESS_PROXY_CONTAINER"] == f"{PROJECT}-{PROXY}-1"
    assert env["SANDBOX_DOCKER_IMAGE"] == app["environment"]["SANDBOX_DOCKER_IMAGE"]
    assert env["SANDBOX_EGRESS_NETWORK"]
    # A failed broker must not keep the product from starting (notes/09 §4.2).
    assert BROKER not in (app.get("depends_on") or {})


@pytest.mark.parametrize("name", WITH_BROKER)
def test_c12_the_proxy_reaches_only_the_gateways_broker_and_kv_store(name: str) -> None:
    rendered = _named(name)
    services = _services(rendered)
    networks = cast("dict[str, dict[str, Any]]", rendered["networks"])
    assert set(_networks(services[PROXY])) == {PLANE, KV}
    for network in (PLANE, KV):
        assert networks[network].get("internal") is True, f"{network} would give the proxy a route off the host"
    assert _members(rendered, PLANE) == {APP, BROKER, PROXY}
    assert _members(rendered, KV) == {PROXY} | (KV_STORES & services.keys())
    assert set(_networks(services[APP])[PLANE].get("aliases") or []) == GATEWAY_ALIASES


@pytest.mark.parametrize("name", WITH_BROKER)
def test_c12_the_proxy_asks_node_for_tokens_at_an_address_it_can_reach(name: str) -> None:
    # The proxy is its own container: the default localhost:3000 is not Node there (E0's NodeGatewayTokenSource).
    services = _services(_named(name))
    url = services[PROXY]["environment"]["AGENT_HARNESS_STATE_URL"]
    host = url.split("//", 1)[1].split(":", 1)[0]
    assert host in (_networks(services[APP])[PLANE].get("aliases") or [])


@pytest.mark.parametrize("name", WITH_BROKER)
def test_h1_ep8_a_composed_proxy_requires_its_store_and_is_given_one(name: str) -> None:
    # The proxy names Node's address, so it refuses to start without Redis and its component key (EGRESS_STATE_REQUIRED
    # follows it, H1; ADR-KEY-02): Compose must hand it both and restart it until Redis answers.
    proxy = _services(_named(name))[PROXY]
    env = proxy["environment"]
    assert env.get("AGENT_HARNESS_STATE_URL") and "EGRESS_STATE_REQUIRED" not in env
    assert {"PIPESHUB_COMPONENT_KEY", "REDIS_HOST", "REDIS_PORT"} <= set(env)
    assert proxy["depends_on"]["redis"]["condition"] == "service_healthy"
    assert proxy["restart"] == "always"


E2E_OVERLAY = REPO_ROOT / "deployment" / "e2e" / "docker-compose.e2e.yml"
E2E_ENV = (
    "E2E_RUN=c12", "E2E_UID=1000", "E2E_GID=1000", "E2E_DOCKER_GID=999", "E2E_SRC=/src", "E2E_SITE_PACKAGES=/site",
    "E2E_NODE_HOME=/node", "E2E_WORK=/work", "E2E_APP_PORT=1", "E2E_LLM_PORT=2", "E2E_SAAS_PORT=3", "E2E_LLM_KEY=k",
    "E2E_LLM_CONTROL=c", "E2E_EGRESS_SUBNETS=10.231.0.0/16", "SANDBOX_DOCKER_IMAGE=pxh-sandbox:x1",
    "E2E_DURABLE=1", "E2E_ENGINE_RUNS=true", "E2E_APP_IMAGE=pxh-e2e-app:c12",
)


def test_e2e2_ds3_the_durable_lane_publishes_the_workers_gateway_by_a_compose_name_the_proxy_reaches() -> None:
    # The proxy runs in its own container on the sandbox plane only: `localhost` there is the proxy, and the
    # worker's own address (gethostbyname of its hostname) is whichever network Docker lists first.
    rendered = _render((str(E2E_OVERLAY),), "graph-neo4j,sandbox,sandbox-broker", E2E_ENV)
    services = _services(rendered)
    env = services[APP]["environment"]
    assert env["HARNESS_ENGINE_RUNS"] == "true"
    url = env["HARNESS_TOOL_GATEWAY_URL"]
    host, _, rest = url.split("//", 1)[1].partition(":")
    assert host in (_networks(services[APP])[PLANE].get("aliases") or [])
    assert host not in ("localhost", "127.0.0.1", "0.0.0.0") and not host.replace(".", "").isdigit()
    assert PLANE in _networks(services[PROXY])
    assert rest.split("/", 1)[0] == env["HARNESS_TOOL_GATEWAY_PORT"]
    # The SaaS the worker calls is on the app's network only: nothing on the sandbox plane reaches it.
    assert set(_networks(services["pxh-fake-saas"])) == {"pipeshub"}


def test_r38ident_fu1_the_lanes_proxy_reads_the_brokers_scope() -> None:
    """The lane overrides the broker's sandbox pool (E2E_EGRESS_SUBNETS): the proxy must read the same one, or it
    refuses every per-sandbox network the broker makes (found on lane pxh-skk)."""
    _the_proxy_and_the_broker_share_a_scope(
        _services(_render((str(E2E_OVERLAY),), "graph-neo4j,sandbox,sandbox-broker", E2E_ENV))
    )


@pytest.mark.parametrize("name", WITH_BROKER)
def test_c12_the_proxy_reaches_the_workers_tool_gateway_by_a_plane_alias(name: str) -> None:
    # Unset, the worker advertised its hostname's address, which depends on Docker's network order (E2E-2).
    services = _services(_named(name))
    url = services[APP]["environment"]["HARNESS_TOOL_GATEWAY_URL"]
    host = url.split("//", 1)[1].split(":", 1)[0]
    assert host in (_networks(services[APP])[PLANE].get("aliases") or [])


@pytest.mark.parametrize("name", WITH_BROKER)
def test_co3_without_the_overlay_the_proxy_offers_no_tunnels(name: str) -> None:
    services = _services(_named(name))
    assert str(services[PROXY]["environment"].get("EGRESS_CONNECT_TUNNELS", "")).lower() in ("", "false")


def test_co3_the_tunnels_overlay_gives_the_proxy_alone_a_way_out() -> None:
    overlays, profiles = RENDERS["slim-broker"]
    rendered = _render((*overlays, TUNNELS), profiles, RENDER_ENV["slim-broker"])
    services = _services(rendered)
    networks = cast("dict[str, dict[str, Any]]", rendered["networks"])
    assert services[PROXY]["environment"]["EGRESS_CONNECT_TUNNELS"] == "true"
    assert set(_networks(services[PROXY])) == {PLANE, KV, EGRESS}
    assert networks[EGRESS].get("internal") is not True
    assert _members(rendered, EGRESS) == {PROXY}
    for network in (PLANE, KV):
        assert networks[network].get("internal") is True
    assert _members(rendered, PLANE) == {APP, BROKER, PROXY}
    assert _socket_holders(rendered) == {DOCKER_PROXY}


def test_cr4_etcd_joins_the_kv_network_when_its_profile_is_on() -> None:
    assert "etcd" in _members(_named("full"), KV)
    assert "etcd" not in _services(_named("slim"))


def test_cr5_docker_mode_gives_the_app_docker_only_through_the_policy_proxy() -> None:
    # main #3782: docker mode never mounts the socket into the app; it lets the app reach the Docker API policy proxy
    # (R38IDENT-FU-1: the only socket holder in either mode), with its own credential. R51-COMPAT: the base file
    # carries it, so an .env from a release without the broker keeps running code with a plain `docker compose up`.
    overlays, profiles = RENDERS["slim"]
    token = "c0ffee" + "9a" * 29
    rendered = _render(overlays, profiles, (f"DOCKER_PROXY_TOKEN={token}",))
    services = _services(rendered)
    assert _socket_holders(rendered) == {DOCKER_PROXY}
    app, proxy = services[APP], services[DOCKER_PROXY]
    assert app["environment"]["DOCKER_HOST"] == f"tcp://{DOCKER_PROXY}:2375"
    assert app["environment"]["DOCKER_PROXY_TOKEN"] == proxy["environment"]["DOCKER_PROXY_TOKEN"] == token
    assert not {"SECRET_KEY", "SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT"} & set(proxy["environment"])
    assert _members(rendered, DOCKER_CONTROL) == {APP, DOCKER_PROXY}
    assert cast("dict[str, dict[str, Any]]", rendered["networks"])[DOCKER_CONTROL].get("internal") is True
    assert app["depends_on"][DOCKER_PROXY] == {"condition": "service_started", "required": False}
    # The proxy reads the profile: a compatibility proxy with no token admits the app tokenless, a hardened one refuses.
    assert proxy["environment"]["PIPESHUB_HARDENED"] == "false"


def test_cr5_the_legacy_overlay_changes_nothing() -> None:
    """Kept so commands that name it keep working (R51-COMPAT)."""
    overlays, profiles = RENDERS["slim"]
    assert _render((*overlays, LEGACY), profiles) == _render(overlays, profiles)


@pytest.mark.parametrize("overlays", [(), (TUNNELS,)], ids=["base", "tunnels"])
@pytest.mark.parametrize("name", WITH_BROKER)
def test_ec5_the_proxy_control_plane_listens_only_where_the_broker_reaches_it(
    name: str, overlays: tuple[str, ...]
) -> None:
    """E2E4: the control listener binds the address of an alias that exists only on sandbox_plane (the one network
    the broker shares with the proxy): never a wildcard, sandbox_kv or the per-sandbox networks. Docker's resolver
    answers a name from one of a container's networks only, so the service name, on two, cannot select it. The proxy
    and the broker read one sandbox subnet pool."""
    base, profiles = RENDERS[name]
    services = _services(_render((*base, *overlays), profiles, RENDER_ENV.get(name, ())))
    broker, proxy = services[BROKER], services[PROXY]
    assert proxy["environment"]["EGRESS_CONTROL_HOST"] == CONTROL_ALIAS
    holding = {net for net, spec in _networks(proxy).items() if CONTROL_ALIAS in (spec.get("aliases") or [])}
    assert holding == {"sandbox_plane"}
    assert set(_networks(broker)) & set(_networks(proxy)) == {"sandbox_plane"}
    assert not any(CONTROL_ALIAS in (spec.get("aliases") or []) for n, svc in services.items() if n != PROXY
                   for spec in _networks(svc).values())
    env = broker["environment"]
    assert proxy["environment"]["SANDBOX_EGRESS_SUBNETS"] == env["SANDBOX_EGRESS_SUBNETS"] == "10.213.0.0/16"


def test_cr6_the_app_tells_node_every_sandbox_plane_address() -> None:
    env = _services(_named("slim"))[APP]["environment"]
    assert env["SANDBOX_BROKER_BACKEND"] == "http://sandbox-broker:8095"
    assert env["EGRESS_PROXY_CONTROL_BACKEND"] == "http://egress-proxy:8098"
    assert env["MODEL_GATEWAY_BACKEND"] == "http://model-gateway:8096"
    # The worker's Tool Gateway listener: the egress proxy's fallback upstream and Node's metrics scrape reach it here
    # (R11-TGW-1; this was 8099, where nothing listens).
    from app.harness.engines.worker import DEFAULT_GATEWAY_PORT

    assert env["TOOL_GATEWAY_BACKEND"] == f"http://tool-gateway:{DEFAULT_GATEWAY_PORT}"
    assert env["HARNESS_TOOL_GATEWAY_URL"].startswith(env["TOOL_GATEWAY_BACKEND"] + "/")


@pytest.mark.parametrize("name", list(RENDERS))
def test_rs13_the_app_publishes_only_its_public_port_and_calls_node_on_the_internal_one(name: str) -> None:
    # H6 C-26: Node's worker-only routes live on AGENT_HARNESS_INTERNAL_PORT (default 3002), never published.
    app = _services(_named(name))[APP]
    assert {int(port["target"]) for port in app.get("ports", [])} == {3000}
    assert app["environment"]["AGENT_HARNESS_STATE_URL"] == "http://localhost:3002"
    assert "AGENT_HARNESS_INTERNAL_PORT" not in app["environment"]


@pytest.mark.parametrize("name", WITH_BROKER)
def test_rs12_the_proxy_asks_nodes_internal_listener_for_tokens(name: str) -> None:
    url = _services(_named(name))[PROXY]["environment"]["AGENT_HARNESS_STATE_URL"]
    assert url.rsplit(":", 1)[1].rstrip("/") == "3002"


def test_cr6_a_blank_sandbox_mode_reaches_the_app_blank() -> None:
    rendered = _render((), "graph-neo4j", ("SANDBOX_MODE=",))
    assert _services(rendered)[APP]["environment"]["SANDBOX_MODE"] == ""

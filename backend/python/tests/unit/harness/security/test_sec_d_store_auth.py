"""SEC-D-06 and SEC-D-08 beyond their proofs (ADR-OPS-03): every store in the shipped Compose files takes a
credential, the installer generates and heals each one, and the app port is local unless the operator opens it.

R51-COMPAT (ADR-OPS-07): the installer's file (docker-compose.yml) no longer refuses to render without a store
credential: an .env from a release without them starts its stores as that release did, each saying so in its log, and
a hardened app refuses them (STORE_AUTH_CHECK follows PIPESHUB_HARDENED). Its app port publishes on every interface
unless APP_BIND_ADDRESS says otherwise (install.sh writes 127.0.0.1 on a hardened install).

They read the shipped files as data and run ``install.sh``'s own functions in bash; no Docker, no network. The lane
probe ``deployment/docker-compose/tests/store_auth_test.sh`` shows the same stores refusing anonymous clients.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[6]
COMPOSE_DIR = REPO / "deployment" / "docker-compose"
MAIN = COMPOSE_DIR / "docker-compose.yml"
INSTALL = COMPOSE_DIR / "install.sh"
ENV_TEMPLATE = COMPOSE_DIR / "env.template"
# The files people install or build from: the installer's, the legacy one and the from-source build.
PRODUCT_FILES = (MAIN, COMPOSE_DIR / "docker-compose.prod-legacy.yml", COMPOSE_DIR / "docker-compose.build.neo4j.yml")
# The two that still refuse to render without a store credential (MAIN falls back, R51-COMPAT).
REQUIRING_FILES = PRODUCT_FILES[1:]
# A store's credential the stores read from the environment on every start (none kept on a volume).
REQUIRED = ("REDIS_PASSWORD", "QDRANT_API_KEY", "MONGO_PASSWORD", "KAFKA_PASSWORD")
# The ones that live on the store's volume: never regenerated over data.
KEPT_ON_VOLUME = {"MONGO_PASSWORD"}
ETCD_CLIENTS = {"etcd_tls_pipeshub_ai": "pipeshub-ai", "etcd_tls_egress_proxy": "egress-proxy", "etcd_tls_sandbox_broker": "sandbox-broker"}


class _ComposeLoader(yaml.SafeLoader):
    """Compose's ``!reset`` and ``!override`` tags read as their plain values."""


def _compose_tag(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


_ComposeLoader.add_multi_constructor("!", _compose_tag)


def _load(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # SafeLoader subclass


def _services(path: Path = MAIN) -> dict[str, dict[str, Any]]:
    return {name: svc or {} for name, svc in _load(path)["services"].items()}


def _env(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(str(item).split("=", 1) if "=" in str(item) else (str(item), "") for item in env)
    return {str(k): str(v) for k, v in env.items()}


def _mounts(service: dict[str, Any]) -> list[str]:
    return [str(v) for v in service.get("volumes") or []]


# --- SEC-D-06: every store takes its credential -----------------------------------------------------------------


@pytest.mark.parametrize("path", REQUIRING_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("name", ["REDIS_PASSWORD", "QDRANT_API_KEY", "MONGO_PASSWORD"])
def test_a_store_credential_is_required_never_defaulted(path: Path, name: str) -> None:
    """Required at least once, and nowhere given a non-empty default or used only when set (``:+``)."""
    text = path.read_text(encoding="utf-8")
    uses = re.findall(r"\$\{" + name + r"(:?[?+-])?([^}]*)\}", text)
    assert any(op == ":?" for op, _ in uses), f"{path.name} never requires {name}"
    defaulted = [rest for op, rest in uses if op in {":-", "-"} and rest]
    assert defaulted == [], f"{path.name} defaults {name} to {defaulted}"
    assert not any(op in {":+", "+"} for op, _ in uses), f"{path.name} uses {name} only when set"


@pytest.mark.parametrize("name", REQUIRED)
def test_r51compat_the_main_file_never_requires_or_defaults_a_store_credential(name: str) -> None:
    """A plain `docker compose up` with an .env from a release without the credential renders (blank), and no store
    is given a value anyone could know."""
    uses = re.findall(r"\$\{" + name + r"(:?[?+-])?([^}]*)\}", MAIN.read_text(encoding="utf-8"))
    assert uses, name
    assert not [op for op, _ in uses if op in {":?", "?"}], f"{name} is required again"
    assert [rest for op, rest in uses if op in {":-", "-"} and rest] == [], f"{name} has a default"


def test_r51compat_each_store_says_when_it_runs_without_its_credential() -> None:
    services = _services()
    redis = str(services["redis"]["command"])
    assert 'if [ -n "$$REDIS_PASSWORD" ]; then set -- --requirepass "$$REDIS_PASSWORD"' in redis
    assert "WARNING: REDIS_PASSWORD is empty" in redis
    mongo = "\n".join(str(part) for part in services["mongodb"]["entrypoint"])
    assert 'if [ "$$1" = "--keyFile" ]; then shift 2; continue; fi' in mongo, "no key (no auth) without a root password"
    assert "WARNING: MONGO_PASSWORD is empty" in mongo
    assert "--keyFile" in services["mongodb"]["command"], "with a password, the key (and so authentication) stays on"
    kafka = "\n".join(str(part) for part in services["kafka-1"]["entrypoint"])
    assert "export KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=ACCESS:PLAINTEXT" in kafka and "WARNING: KAFKA_PASSWORD" in kafka
    assert _env(services["kafka-1"])["PXH_KAFKA_SASL_LOGIN"] == "${KAFKA_PASSWORD:+true}"
    assert "exit 0" in str(services["kafka-init"]["command"]), "kafka-init writes no login without a password"
    assert _env(services["qdrant"])["QDRANT__SERVICE__API_KEY"] == "${QDRANT_API_KEY:-}"


@pytest.mark.parametrize("path", PRODUCT_FILES, ids=lambda p: p.name)
def test_no_product_file_ships_a_published_placeholder_credential(path: Path) -> None:
    placeholders = re.findall(r"\$\{[A-Z0-9_]*(?:PASSWORD|API_KEY|SECRET)[A-Z0-9_]*:-(?:password|your_[a-z_]*|changeme|secret|admin)\}", path.read_text(encoding="utf-8"))
    assert placeholders == []


@pytest.mark.parametrize("path", REQUIRING_FILES, ids=lambda p: p.name)
def test_redis_always_requires_its_password_and_signs_in_its_own_checks(path: Path) -> None:
    redis = _services(path)["redis"]
    command = str(redis["command"])
    assert '--requirepass "$$REDIS_PASSWORD"' in command
    assert ":+" not in command and "${REDIS_PASSWORD" not in command, "the password must come from the environment"
    env = _env(redis)
    assert env["REDIS_PASSWORD"].startswith("${REDIS_PASSWORD:?")
    assert "REDISCLI_AUTH" in env
    assert " -a " not in str(redis.get("healthcheck", {}).get("test")), "no password on the healthcheck's command line"


def test_kafka_takes_only_the_scram_login() -> None:
    services = _services()
    kafka = _env(services["kafka-1"])
    protocols = [entry.split(":", 1)[1] for entry in kafka["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"].split(",")]
    assert protocols and all(p in {"SASL_PLAINTEXT", "SASL_SSL"} for p in protocols), protocols
    assert kafka["KAFKA_SASL_ENABLED_MECHANISMS"] == "SCRAM-SHA-512"
    assert kafka["KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL"] == "SCRAM-SHA-512"
    assert "KAFKA_PASSWORD" not in kafka and "KAFKA_USERNAME" not in kafka, "cp-kafka writes KAFKA_* into server.properties"
    assert services["kafka-1"]["depends_on"]["kafka-init"]["condition"] == "service_completed_successfully"
    init = _env(services["kafka-init"])
    assert init["KAFKA_SCRAM_PASSWORD"] == "${KAFKA_PASSWORD:-}"
    app = _env(services["pipeshub-ai"])
    # Without a password (compatibility) the app signs in with nothing, as the PLAINTEXT broker expects.
    assert app["KAFKA_USERNAME"] == "${KAFKA_USERNAME-${KAFKA_PASSWORD:+pipeshub}}"
    assert app["KAFKA_SASL_MECHANISM"] == "${KAFKA_SASL_MECHANISM:-scram-sha-512}"


def test_zookeeper_is_reachable_by_kafka_alone() -> None:
    data = _load(MAIN)
    on_zk = sorted(name for name, svc in data["services"].items() if "broker_zk" in ((svc or {}).get("networks") or []))
    assert on_zk == ["kafka-1", "kafka-init", "zookeeper"]
    assert data["services"]["zookeeper"]["networks"] == ["broker_zk"]
    assert data["networks"]["broker_zk"]["internal"] is True


def test_etcd_serves_only_tls_clients_with_a_certificate() -> None:
    command = " ".join(str(_services()["etcd"]["command"]).split())
    for flag in ("--client-cert-auth", "--trusted-ca-file /tls/ca.crt", "--cert-file /tls/tls.crt", "--key-file /tls/tls.key"):
        assert flag in command
    assert "--listen-client-urls https://0.0.0.0:2379" in command
    assert "--advertise-client-urls https://etcd:2379" in command
    assert "--listen-peer-urls http://127.0.0.1:2380" in command, "the lone member's peer port stays on loopback"
    assert "http://0.0.0.0" not in command


def test_each_etcd_certificate_is_mounted_by_its_owner_alone() -> None:
    services = _services()
    mounted: dict[str, set[str]] = {}
    for name, svc in services.items():
        for mount in _mounts(svc):
            volume = mount.split(":", 1)[0]
            if volume.startswith("etcd_tls_"):
                mounted.setdefault(volume, set()).add(name)
    assert mounted["etcd_tls_ca"] == {"etcd-tls"}, "the CA key reaches no consumer"
    assert mounted["etcd_tls_server"] == {"etcd-tls", "etcd"}
    for volume, owner in ETCD_CLIENTS.items():
        assert mounted[volume] == {"etcd-tls", owner}, volume
    issuer = services["etcd-tls"]
    assert issuer.get("network_mode") == "none"
    assert issuer.get("cap_add") == ["CHOWN"]
    assert services["etcd"]["depends_on"]["etcd-tls"]["condition"] == "service_completed_successfully"


@pytest.mark.parametrize("service", ["pipeshub-ai", "egress-proxy", "sandbox-broker"])
def test_every_etcd_client_uses_https_and_its_own_certificate(service: str) -> None:
    env = _env(_services()[service])
    assert env["ETCD_URL"] == "https://etcd:2379"
    assert {env["ETCD_CA_FILE"], env["ETCD_CERT_FILE"], env["ETCD_KEY_FILE"]} == {
        "/run/pipeshub/etcd-tls/ca.crt", "/run/pipeshub/etcd-tls/tls.crt", "/run/pipeshub/etcd-tls/tls.key",
    }
    assert any(m.endswith(":/run/pipeshub/etcd-tls:ro") for m in _mounts(_services()[service]))


def test_the_app_refuses_an_open_store_by_default() -> None:
    """No shipped service sets STORE_AUTH_CHECK, even to a default: only an operator relaxes it."""
    for path in PRODUCT_FILES:
        for name, svc in _services(path).items():
            assert "STORE_AUTH_CHECK" not in _env(svc), f"{path.name} {name}"


def test_env_template_ships_every_store_credential_empty() -> None:
    lines = dict(
        line.split("=", 1) for line in ENV_TEMPLATE.read_text(encoding="utf-8").splitlines() if re.match(r"^[A-Z0-9_]+=", line)
    )
    for name in (*REQUIRED, "NEO4J_PASSWORD", "ARANGO_PASSWORD"):
        assert lines.get(name) == "", f"env.template ships {name}={lines.get(name)!r}"


# --- the installer generates and heals them ---------------------------------------------------------------------


def _install_function(*names: str) -> str:
    text = INSTALL.read_text(encoding="utf-8")
    bodies = []
    for name in names:
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.M | re.S)
        assert match, name
        bodies.append(match.group(0))
    return "\n".join(bodies)


def _regenerable() -> str:
    text = INSTALL.read_text(encoding="utf-8")
    match = re.search(r"^REGENERABLE_CREDENTIALS=\(\n.*?^\)\n", text, re.M | re.S)
    assert match
    return match.group(0)


def test_the_wizard_generates_every_required_credential() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for name in REQUIRED:
        assert re.search(rf'^\s+{name}="\$\(get_existing_val {name} "\$\(gen_secret \d+\)"\)"', text, re.M), name


def test_an_upgrade_heals_every_required_credential_a_store_reads_fresh() -> None:
    listed = set(re.findall(r'"([A-Z_]+) \d+"', _regenerable()))
    assert (set(REQUIRED) - KEPT_ON_VOLUME) <= listed, set(REQUIRED) - KEPT_ON_VOLUME - listed
    assert not (listed & KEPT_ON_VOLUME), "a password kept on a store's volume must never be regenerated"
    assert {"EGRESS_PROXY_COMPONENT_KEY", "SANDBOX_BROKER_COMPONENT_KEY", "DOCKER_PROXY_BROKER_TOKEN"} <= listed
    # The app's proxy token follows the code-execution mode (sync_docker_proxy_app_token), never healed blindly.
    assert "DOCKER_PROXY_TOKEN" not in listed


def _bash(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")
def test_heal_generates_only_what_is_missing_and_never_prints_a_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_PASSWORD=keep-this-one\nQDRANT_API_KEY=\nOTHER=1\n", encoding="utf-8")
    script = "\n".join([
        "set -euo pipefail",
        f'ENV_FILE="{env_file}"',
        "info() { printf 'INFO %s\\n' \"$*\"; }",
        _install_function("gen_secret", "get_existing_val", "persist_env_var", "heal_regenerable_credentials"),
        _regenerable(),
        "heal_regenerable_credentials",
        "echo \"SHELL_KAFKA=${KAFKA_PASSWORD}\"",
    ])
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    written = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines())
    assert written["REDIS_PASSWORD"] == "keep-this-one"
    assert written["OTHER"] == "1"
    for name, length in (("QDRANT_API_KEY", 40), ("KAFKA_PASSWORD", 48), ("EGRESS_PROXY_COMPONENT_KEY", 64)):
        assert re.fullmatch(rf"[a-f0-9]{{{length}}}", written[name]), name
    out = result.stdout.replace(f"SHELL_KAFKA={written['KAFKA_PASSWORD']}", "")
    assert "Generated QDRANT_API_KEY" in out and "REDIS_PASSWORD" not in out
    for name in ("QDRANT_API_KEY", "KAFKA_PASSWORD", "EGRESS_PROXY_COMPONENT_KEY"):
        assert written[name] not in out, f"{name}'s value was printed"


# --- SEC-D-08: the app port is local unless the operator opens it ------------------------------------------------


@pytest.mark.parametrize("path", PRODUCT_FILES, ids=lambda p: p.name)
def test_no_product_file_publishes_a_port_on_every_interface(path: Path) -> None:
    for name, svc in _services(path).items():
        for port in svc.get("ports") or []:
            spec = str(port)
            host = spec.rsplit(":", 2)[0] if spec.count(":") >= 2 else ""
            if path == MAIN and name == "pipeshub-ai":
                continue  # the compatibility default, below
            assert host.startswith(("127.0.0.1", "${APP_BIND_ADDRESS:-127.0.0.1}")), f"{path.name} {name} publishes {spec}"


def test_the_app_port_takes_the_bind_address() -> None:
    # Unset publishes on every interface, as a release without APP_BIND_ADDRESS did (R51-COMPAT); install.sh writes
    # 127.0.0.1 on a hardened install without a public URL.
    (port,) = _services()["pipeshub-ai"]["ports"]
    assert port == "${APP_BIND_ADDRESS:-0.0.0.0}:${APP_PORT:-3000}:3000"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")
@pytest.mark.parametrize(
    ("explicit", "public_url", "expected"),
    [
        ("", "", "127.0.0.1"),
        ("", "https://pipeshub.example.com", "0.0.0.0"),  # the installer's documented choice
        ("", "http://pipeshub.example.com:8080/", "0.0.0.0"),
        ("", "http://localhost:3000", "127.0.0.1"),
        ("", "HTTP://LOCALHOST", "127.0.0.1"),
        ("", "http://127.0.0.1:3000", "127.0.0.1"),
        ("", "http://[::1]:3000", "127.0.0.1"),
        ("", "https://localhost.example.com", "0.0.0.0"),  # a real host that starts like localhost
        ("127.0.0.1", "https://pipeshub.example.com", "127.0.0.1"),
        ("10.0.0.5", "", "10.0.0.5"),
    ],
)
def test_bind_address_rule(explicit: str, public_url: str, expected: str) -> None:
    script = _install_function("app_bind_address") + f'\napp_bind_address "{explicit}" "{public_url}"'
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")
@pytest.mark.parametrize(
    ("address", "url", "says", "never"),
    [
        ("127.0.0.1", "", "only this machine can reach it", "plain HTTP"),
        ("0.0.0.0", "https://p.example.com", "bypass ufw and firewalld", "unencrypted"),
        ("0.0.0.0", "http://p.example.com", "unencrypted", "only this machine"),
    ],
)
def test_the_installer_says_what_the_bind_address_means(address: str, url: str, says: str, never: str) -> None:
    script = "\n".join([
        "info() { printf '%s\\n' \"$*\"; }", "warn() { printf '%s\\n' \"$*\"; }",
        _install_function("explain_bind_address"), f'explain_bind_address "{address}" 3000 "{url}"',
    ])
    result = _bash(script)
    assert says in result.stdout and never not in result.stdout, result.stdout
    assert "APP_BIND_ADDRESS" in result.stdout, "it names the setting that changes it"


def test_the_installer_no_longer_claims_local_only_without_checking() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "local access only" not in text
    assert "APP_BIND_ADDRESS=${APP_BIND_ADDRESS}" in text

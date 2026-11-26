"""SEC-D-03 and SEC-D-05 beyond their proofs: every shipped Compose file keeps the root key off the sandbox plane,
and the installer's keys pass the checks the services make (ADR-KEY-02).

They read the shipped files as data and run ``install.sh``'s own functions in bash; no Docker, no network.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.utils.encryption.secret_key_ring import secret_key_ring, weak_secret_reason

REPO = Path(__file__).resolve().parents[6]
COMPOSE_DIR = REPO / "deployment" / "docker-compose"
INSTALL = COMPOSE_DIR / "install.sh"
SANDBOX_PLANE = ("egress-proxy", "sandbox-broker")
# Everything that faces sandbox code or holds the Docker socket (R38-IDENT: the legacy socket proxy was given
# SECRET_KEY to derive its caller credential, and a check that named only the sandbox plane let it through).
HOLDS_NO_ROOT_KEY = (*SANDBOX_PLANE, "docker-socket-proxy")
RING = ("SECRET_KEY", "SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT")
# R38IDENT-FU-8: the ring by file is the ring too.
ROOT_ENV = {*RING, *(f"{name}_FILE" for name in RING), "REDIS_PASSWORD", "REDIS_USERNAME"}


def _compose_files() -> list[Path]:
    return sorted(
        p for p in [*(REPO / "deployment").rglob("docker-compose*.yml"), *(REPO / "deployment").rglob("docker-compose*.yaml")]
        if "node_modules" not in p.parts
    )


class _ComposeLoader(yaml.SafeLoader):
    """Compose's ``!reset`` and ``!override`` tags read as their plain values."""


def _compose_tag(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


_ComposeLoader.add_multi_constructor("!", _compose_tag)


def _env(service: dict[str, Any]) -> set[str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return {str(item).split("=", 1)[0] for item in env}
    return {str(k) for k in env}


@pytest.mark.parametrize("path", _compose_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_compose_file_gives_the_sandbox_plane_the_root_key_or_the_shared_redis_login(path: Path) -> None:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader) or {}
    for name, service in (data.get("services") or {}).items():
        if name in HOLDS_NO_ROOT_KEY and isinstance(service, dict):
            assert not (_env(service) & ROOT_ENV), f"{path.name}: {name} holds {sorted(_env(service) & ROOT_ENV)}"
            # A Compose secret is a file in the container: the root key's must not reach the plane that way either.
            assert not service.get("secrets"), f"{path.name}: {name} mounts secrets {service.get('secrets')}"


def test_the_secret_key_file_override_gives_the_app_a_0400_file_and_no_variable() -> None:
    """R38IDENT-FU-8 (ADR-KEY-02 D8): the opt-in Compose file. The key leaves the app's environment and arrives as a
    secret owned by the app's user, mode 0400, which SECRET_KEY_FILE names."""
    data = yaml.load((COMPOSE_DIR / "docker-compose.secret-key-file.yml").read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert set(data["services"]) == {"pipeshub-ai"}
    app = data["services"]["pipeshub-ai"]
    env = dict(str(item).split("=", 1) for item in app["environment"])
    assert env["SECRET_KEY"] == ""
    (mount,) = app["secrets"]
    assert env["SECRET_KEY_FILE"] == f"/run/secrets/{mount['target']}"
    assert (str(mount["uid"]), str(mount["gid"]), mount["mode"]) == ("1000", "1000", 0o400)
    assert data["secrets"][mount["source"]] == {"environment": "SECRET_KEY"}
    # The base file still passes SECRET_KEY as a variable, which the override empties: an existing install is unchanged.
    base = yaml.load((COMPOSE_DIR / "docker-compose.yml").read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert "SECRET_KEY=${SECRET_KEY:-}" in base["services"]["pipeshub-ai"]["environment"]
    assert "secrets" not in base["services"]["pipeshub-ai"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker compose CLI (no daemon)")
def test_compose_renders_the_override_with_the_key_only_in_the_secret(tmp_path: Path) -> None:
    env_file = tmp_path / "render.env"
    marker = "render-only-value-not-a-key-0123456789"
    env_file.write_text(
        f"SECRET_KEY={marker}\nREDIS_PASSWORD=x\nQDRANT_API_KEY=x\nMONGO_PASSWORD=x\nKAFKA_PASSWORD=x\n"
        "NEO4J_PASSWORD=x\nDOCKER_PROXY_TOKEN=x\nDOCKER_PROXY_BROKER_TOKEN=x\n",
        encoding="utf-8",
    )
    rendered = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.secret-key-file.yml",
         "--env-file", str(env_file), "config", "--format", "json"],
        cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=60, check=False,
    )
    if rendered.returncode != 0 and "unknown flag" in rendered.stderr:
        pytest.skip("docker compose without config --format")
    assert rendered.returncode == 0, rendered.stderr[-2000:]
    config = json.loads(rendered.stdout)
    app = config["services"]["pipeshub-ai"]
    assert app["environment"]["SECRET_KEY"] == ""
    assert app["environment"]["SECRET_KEY_FILE"] == "/run/secrets/pipeshub_secret_key"
    assert marker not in json.dumps(app)
    assert [s["source"] for s in app["secrets"]] == ["pipeshub_secret_key"]
    for name in HOLDS_NO_ROOT_KEY:
        if name in config["services"]:
            assert not config["services"][name].get("secrets"), name


def test_the_main_compose_file_gives_each_component_its_own_key_and_the_app_both() -> None:
    services = yaml.safe_load((COMPOSE_DIR / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    for name, node_env in (("egress-proxy", "EGRESS_PROXY_COMPONENT_KEY"), ("sandbox-broker", "SANDBOX_BROKER_COMPONENT_KEY")):
        env = services[name]["environment"]
        assert env["PIPESHUB_COMPONENT"] == name
        assert env["PIPESHUB_COMPONENT_KEY"] == f"${{{node_env}:-}}"
        assert f"{node_env}=${{{node_env}:-}}" in services["pipeshub-ai"]["environment"]


def test_env_template_ships_no_secret_key_value() -> None:
    text = (COMPOSE_DIR / "env.template").read_text(encoding="utf-8")
    assert re.search(r"^SECRET_KEY=$", text, re.M)
    assert re.search(r"^EGRESS_PROXY_COMPONENT_KEY=$", text, re.M)
    assert re.search(r"^SANDBOX_BROKER_COMPONENT_KEY=$", text, re.M)


def _install_function(*names: str) -> str:
    text = INSTALL.read_text(encoding="utf-8")
    bodies = []
    for name in names:
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.M | re.S)
        assert match, name
        bodies.append(match.group(0))
    return "\n".join(bodies)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")
def test_install_sh_generates_keys_the_services_accept() -> None:
    script = _install_function("gen_secret") + "\nfor _ in 1 2 3 4 5 6 7 8; do gen_secret 32; echo; done\n"
    keys = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True, timeout=30).stdout.split()
    assert len(set(keys)) == 8
    for key in keys:
        assert weak_secret_reason(key) is None
        secret_key_ring({"SECRET_KEY": key})


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")
@pytest.mark.parametrize(
    ("key", "flagged"),
    [
        ("your_random_encryption_secret_key", True),
        ("YOUR-RANDOM-ENCRYPTION-SECRET-KEY-2024", True),
        ("changeme", True),
        ("short-but-random-9f8e", True),
        ("a" * 40, True),
        ("e3b98a4da31a127d4bde6e43033f66ba274cab0eb7eb1c70ec41402bf6273dd8", False),
    ],
)
def test_install_sh_flags_what_the_services_refuse(key: str, flagged: bool) -> None:
    script = _install_function("weak_secret_reason") + '\nweak_secret_reason "$1"\n'
    reason = subprocess.run(["bash", "-c", script, "x", key], capture_output=True, text=True, check=True, timeout=30).stdout
    assert bool(reason.strip()) is flagged
    if flagged:
        assert weak_secret_reason(key) is not None


def test_install_sh_keeps_the_old_key_readable_and_writes_the_component_keys() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert 'SECRET_KEY_PREVIOUS="$SECRET_KEY"' in text
    for line in ("SECRET_KEY_PREVIOUS=${SECRET_KEY_PREVIOUS}", "EGRESS_PROXY_COMPONENT_KEY=${EGRESS_PROXY_COMPONENT_KEY}",
                 "SANDBOX_BROKER_COMPONENT_KEY=${SANDBOX_BROKER_COMPONENT_KEY}"):
        assert line in text


_PROXY_TOKENS = ("DOCKER_PROXY_TOKEN", "DOCKER_PROXY_BROKER_TOKEN")
_BASE_COMPOSE = REPO / "deployment" / "docker-compose" / "docker-compose.yml"


def _services_of(path: Path) -> dict[str, Any]:
    return (yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader) or {}).get("services") or {}


@pytest.mark.parametrize("path", _compose_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_the_socket_proxy_and_each_caller_share_a_generated_token(path: Path) -> None:
    """SEC-A-04 under ADR-KEY-02, R38IDENT-FU-1: the proxy beside the Docker socket checks generated credentials, one
    per caller (the legacy app's, the sandbox broker's), and every service that calls it sends one the proxy holds. An
    overlay is read over the base file it extends, as Compose merges it."""
    services = _services_of(path)
    proxy = services.get("docker-socket-proxy")
    callers = [name for name, svc in services.items()
               if isinstance(svc, dict) and "tcp://docker-socket-proxy" in " ".join(map(str, svc.get("environment") or []))]
    if not isinstance(proxy, dict) and not callers:
        return
    overlay = path.parent.name == "e2e" or path.name == "docker-compose.sandbox-legacy.yml"
    base = _services_of(_BASE_COMPOSE) if overlay else {}
    proxy_env = _env(proxy or {}) | _env(base.get("docker-socket-proxy") or {})
    assert proxy_env & set(_PROXY_TOKENS), f"{path.name}: the proxy has no caller credential"
    for name in callers:
        sent = _env(services[name]) | _env(base.get(name) or {})
        assert sent & proxy_env & set(_PROXY_TOKENS), f"{path.name}: {name} calls the proxy without a token it holds"


def test_the_installer_and_the_lane_generate_the_legacy_proxy_token() -> None:
    install = INSTALL.read_text(encoding="utf-8")
    assert 'DOCKER_PROXY_TOKEN="$(get_existing_val DOCKER_PROXY_TOKEN "$(gen_secret 32)")"' in install
    assert "\nDOCKER_PROXY_TOKEN=${DOCKER_PROXY_TOKEN}\n" in install
    assert "DOCKER_PROXY_TOKEN=$(rand 32)" in (REPO / "deployment" / "e2e" / "run.sh").read_text(encoding="utf-8")


def test_the_installer_and_the_lane_generate_and_keep_the_brokers_proxy_token() -> None:
    """R38IDENT-FU-1: the broker's credential at the proxy is generated, kept once set, and healed into an older .env."""
    install = INSTALL.read_text(encoding="utf-8")
    assert 'DOCKER_PROXY_BROKER_TOKEN="$(get_existing_val DOCKER_PROXY_BROKER_TOKEN "$(gen_secret 32)")"' in install
    assert "\nDOCKER_PROXY_BROKER_TOKEN=${DOCKER_PROXY_BROKER_TOKEN}\n" in install
    assert '"DOCKER_PROXY_BROKER_TOKEN 32"' in install
    lane = (REPO / "deployment" / "e2e" / "run.sh").read_text(encoding="utf-8")
    assert "DOCKER_PROXY_BROKER_TOKEN=$(rand 32)" in lane and "DOCKER_PROXY_BROKER_TOKEN:32" in lane

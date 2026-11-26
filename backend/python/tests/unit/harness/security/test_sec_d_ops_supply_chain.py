"""R37-SECD proofs: operations, deployment and supply chain.

Each case states the control a hardened deployment must have and is expected to fail on today's tree
(``xfail(strict=True)``): the gate stays green until the fix lands, and an xpass turns it red so the marker is dropped
in the same change. They read the shipped Compose files, the Helm chart, the Dockerfiles and the workflows as data, so
they need no Docker, network or running stack.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.utils.encryption.secret_key_ring import SecretKeyRingError, secret_key_ring

REPO = Path(__file__).resolve().parents[6]
COMPOSE = REPO / "deployment" / "docker-compose" / "docker-compose.yml"
CHART = REPO / "deployment" / "helm" / "pipeshub-ai"
WORKFLOWS = REPO / ".github" / "workflows"
RULES = REPO / "deployment" / "monitoring" / "prometheus" / "agent-harness.rules.yml"
APP_TS = REPO / "backend" / "nodejs" / "apps" / "src" / "app.ts"
IMAGE_PINS = REPO / "contracts" / "supply-chain" / "image-pins.json"

SANDBOX_PLANE = ("egress-proxy", "sandbox-broker")


def _sec(finding: str, why: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=f"{finding}: {why}")


def _services() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return {name: svc or {} for name, svc in data["services"].items()}


def _env_names(service: dict[str, Any]) -> set[str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return {item.split("=", 1)[0] for item in env}
    return set(env)


def _values() -> dict[str, Any]:
    return yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))


# --- SEC-D-02: the sandbox-facing services run as root with Docker's default capabilities -----------------------


@pytest.mark.parametrize("name", SANDBOX_PLANE)
def test_sec_d_02_sandbox_plane_services_run_unprivileged(name: str) -> None:
    svc = _services()[name]
    user = str(svc.get("user") or "")
    assert user and not user.startswith(("0", "root")), f"{name} runs as root"
    assert svc.get("cap_drop") == ["ALL"], f"{name} keeps Docker's default capabilities (NET_RAW, DAC_OVERRIDE, ...)"
    assert "no-new-privileges:true" in (svc.get("security_opt") or []), f"{name} may gain privileges"
    assert svc.get("read_only") is True, f"{name} has a writable root filesystem"


# --- SEC-D-03: the sandbox-facing services hold the platform root key -------------------------------------------


@pytest.mark.parametrize("name", SANDBOX_PLANE)
def test_sec_d_03_sandbox_plane_holds_no_platform_root_key(name: str) -> None:
    assert "SECRET_KEY" not in _env_names(_services()[name])


def test_sec_d_03_helm_broker_holds_no_platform_root_key() -> None:
    template = (CHART / "templates" / "sandbox-broker.yaml").read_text(encoding="utf-8")
    assert not re.search(r"name:\s*SECRET_KEY\b", template)


# --- SEC-D-04: first-party images are unsigned and run by mutable tag -------------------------------------------


def test_sec_d_04_release_signs_images() -> None:
    text = (WORKFLOWS / "release-publish.yml").read_text(encoding="utf-8")
    assert "cosign sign" in text or "attest-build-provenance" in text
    jobs = yaml.safe_load(text)["jobs"]
    # No tag moves before the images are scanned and (once signing is on) signed by digest.
    assert {"scan", "sign"} <= set(jobs["promote"]["needs"]) and "if" not in jobs["sign"]
    assert jobs["sign"]["permissions"].get("id-token") == "write"


def test_sec_d_04_installer_verifies_images() -> None:
    text = (REPO / "deployment" / "docker-compose" / "install.sh").read_text(encoding="utf-8")
    assert "cosign verify" in text
    # The check is made on the release workflow's identity and its result pins what Compose runs.
    assert "--certificate-identity-regexp" in text and "release-publish" in text
    assert 'pin_release_images "$_DO_PULL"' in text
    assert "${PIPESHUB_APP_PINNED:-" in COMPOSE.read_text(encoding="utf-8")


def test_sec_d_04_helm_pins_first_party_images() -> None:
    values = _values()
    assert values["image"]["tag"] != "latest"
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    block = helpers.split('define "pipeshub-ai.image"', 1)[1].split("{{- end }}", 1)[0]
    assert "digest" in block


# --- SEC-D-05: SECRET_KEY strength is not enforced --------------------------------------------------------------


@pytest.mark.parametrize("weak", ["your_random_encryption_secret_key", "changeme", "abc"])
def test_sec_d_05_weak_secret_key_refused(weak: str) -> None:
    with pytest.raises(SecretKeyRingError):
        secret_key_ring({"SECRET_KEY": weak})


def test_sec_d_05_no_compose_defaults_secret_key() -> None:
    offenders = [
        str(p.relative_to(REPO))
        for p in (REPO / "deployment" / "docker-compose").rglob("*.yml")
        if "SECRET_KEY:-your_random_encryption_secret_key" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --- SEC-D-06: store authentication is optional or absent -------------------------------------------------------


def test_sec_d_06_redis_auth_is_unconditional() -> None:
    assert "${REDIS_PASSWORD:+" not in str(_services()["redis"]["command"])


def test_sec_d_06_etcd_requires_client_auth() -> None:
    command = str(_services()["etcd"]["command"])
    assert "--client-cert-auth" in command and "https://" in command


def test_sec_d_06_kafka_requires_auth() -> None:
    env = _services()["kafka-1"]["environment"]
    protocol_map = next(e for e in env if e.startswith("KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="))
    assert "PLAINTEXT" not in protocol_map.replace("SASL_PLAINTEXT", "")


# --- SEC-D-07: browser security headers are opt-in ---------------------------------------------------------------


def test_sec_d_07_security_headers_on_by_default() -> None:
    env = _services()["pipeshub-ai"]["environment"]
    assert "STRICT_MODE=${STRICT_MODE:-false}" not in env


def test_sec_d_07_no_powered_by_header() -> None:
    assert re.search(r"this\.app\.disable\(\s*['\"]x-powered-by['\"]\s*\)", APP_TS.read_text(encoding="utf-8"))


# --- SEC-D-08: the app is published on every interface ----------------------------------------------------------


def test_sec_d_08_app_port_bound_to_loopback_by_default() -> None:
    """R51-COMPAT (ADR-OPS-07): loopback is the hardened default install.sh writes (a fresh install, and an opt-in);
    Compose's own default is every interface, as a release without APP_BIND_ADDRESS published it."""
    (port,) = _services()["pipeshub-ai"]["ports"]
    assert str(port) == "${APP_BIND_ADDRESS:-0.0.0.0}:${APP_PORT:-3000}:3000"
    install = (REPO / "deployment" / "docker-compose" / "install.sh").read_text(encoding="utf-8")
    assert 'if [[ "$PIPESHUB_HARDENED" == true ]]; then\n    APP_BIND_ADDRESS="$(app_bind_address' in install
    template = (REPO / "deployment" / "docker-compose" / "env.template").read_text(encoding="utf-8")
    assert "\nAPP_BIND_ADDRESS=127.0.0.1\n" in template and "\nPIPESHUB_HARDENED=true\n" in template


# --- SEC-D-09: Helm has no sandbox_plane and no default-deny network policy --------------------------------------


def test_sec_d_09_helm_network_policy_follows_the_profile() -> None:
    # Unset follows `hardened` (ADR-OPS-07): on for a hardened release, off for one upgraded from a release without it.
    assert _values()["networkPolicy"]["enabled"] is None
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert "hardened" in helpers


def test_sec_d_09_helm_broker_egress_restricted() -> None:
    template = (CHART / "templates" / "sandbox-broker.yaml").read_text(encoding="utf-8")
    policy = template.split("kind: NetworkPolicy", 1)[1]
    assert re.search(r"policyTypes:\s*\n\s*-\s*Ingress\s*\n\s*-\s*Egress", policy)


# --- SEC-D-10: product containers miss baseline hardening --------------------------------------------------------


def test_sec_d_10_every_compose_service_hardened() -> None:
    missing = sorted(
        name for name, svc in _services().items()
        if "no-new-privileges:true" not in (svc.get("security_opt") or []) or not svc.get("cap_drop")
    )
    assert missing == []


def test_sec_d_10_helm_containers_hardened() -> None:
    # The read-only root and Qdrant's non-root context follow `hardened` (ADR-OPS-07); what a hardened release renders
    # is pinned by test_container_baseline.py's Helm renders and deployment/helm/tests/test_security_profile.py.
    values = _values()
    assert values["securityContext"].get("readOnlyRootFilesystem") in (None, True)
    assert values["serviceAccount"].get("automountServiceAccountToken") is False


# --- SEC-D-11: what scripts/supply_chain.py does not check -------------------------------------------------------

_PIPE_TO_SHELL = re.compile(r"curl\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba)?sh\b")


def _dockerfiles() -> list[Path]:
    return [p for p in REPO.rglob("Dockerfile*")
            if "node_modules" not in p.parts and p.relative_to(REPO).parts[0] not in ("docs", ".claude")]


def test_sec_d_11_no_pipe_to_shell_in_image_builds() -> None:
    hits = [f"{p.relative_to(REPO)}:{n}" for p in _dockerfiles()
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if _PIPE_TO_SHELL.search(line)]
    assert hits == []


def test_sec_d_11_user_run_service_tags_are_exact() -> None:
    import json

    pins = json.loads(IMAGE_PINS.read_text(encoding="utf-8"))["tagOnly"]
    floating = [i["ref"] for i in pins if i["category"] == "user-run-service"
                and not re.search(r":v?\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?$|:\d{4}\.\d+\.\d+-[0-9a-f]+$", i["ref"])]
    assert floating == []


def test_sec_d_11_helm_third_party_images_pinned() -> None:
    values = (CHART / "values.yaml").read_text(encoding="utf-8")
    assert '"docker:27-dind"' not in values
    assert not re.search(r"^\s*tag:\s*v1\.15\s*$", values, re.M)


def test_sec_d_11_workflow_actions_pinned_by_sha() -> None:
    unpinned = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^\s*-?\s*uses:\s*([^\s#]+)", line)
            if m and not m.group(1).startswith("./") and not re.search(r"@[0-9a-f]{40}$", m.group(1)):
                unpinned.append(f"{path.name}:{n} {m.group(1)}")
    assert unpinned == []


# --- SEC-D-12: CI workflows ---------------------------------------------------------------------------------------


def test_sec_d_12_no_inputs_expanded_in_shell() -> None:
    hits = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        in_run = False
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"^\s*-?\s*run:\s*[|>]", line):
                in_run, indent = True, len(line) - len(line.lstrip())
                continue
            if in_run and line.strip() and len(line) - len(line.lstrip()) <= indent:
                in_run = False
            if in_run and re.search(r"\$\{\{\s*(?:inputs\.|github\.event\.inputs\.)", line):
                hits.append(f"{path.name}:{n}")
    assert hits == []


def test_sec_d_12_changelog_agent_has_no_interpreter() -> None:
    assert "Bash(python3:*)" not in (WORKFLOWS / "release-changelog.yml").read_text(encoding="utf-8")


def test_sec_d_12_codeowners_guard_workflows() -> None:
    owners = REPO / ".github" / "CODEOWNERS"
    assert owners.is_file() and "/.github/workflows/" in owners.read_text(encoding="utf-8")


# --- SEC-D-13: no detection for automated attacks ----------------------------------------------------------------


def test_sec_d_13_attack_alerts_exist() -> None:
    rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    names = {r.get("alert") for g in rules["groups"] for r in g["rules"]}
    wanted = {"PipesHubAuthFailureBurst", "HarnessEgressRefusalBurst", "HarnessGatewayTokenRejected",
              "HarnessApprovalRequestFlood", "HarnessKillSwitchEngaged"}
    assert wanted <= names, sorted(wanted - names)

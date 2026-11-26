"""The air-gapped install guide's settings are the ones the shipped files honour (WP-87, C15.04, OP-04).

guides/air-gapped-install.md tells an operator how to take every image from a mirror and run with no route out.
Each claim is checked against what the deployment actually does with it:

- AG-1: with the documented Compose settings, every PipesHub image the Compose file starts comes from the mirror,
  and the guide's table of store images is exactly the Compose file's images that have no registry setting.
- AG-2: the Helm chart rendered with the guide's own values block names only mirror images (needs ``helm``).
- AG-3: the guide's engine-image table is the release allowlist, and the broker runs an engine whose image was
  loaded under that name without asking any registry, while a copy under a mirror name leaves it unavailable.
- AG-4: the offline-model and no-pull settings reach what reads them.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import yaml

from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

GUIDE = REPO_ROOT / "docs/agent-harness-v2/guides/air-gapped-install.md"
COMPOSE = REPO_ROOT / "deployment/docker-compose/docker-compose.yml"
CHART = REPO_ROOT / "deployment/helm"
MIRROR = "registry.internal:5000"
VAR = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::?-([^${}]*))?\}")


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _table(heading: str) -> list[list[str]]:
    section = _guide().split(heading, 1)[1]
    rows = []
    for line in section.splitlines():
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif rows:
            break
    return rows[2:]


def _interpolate(value: str, env: dict[str, str]) -> str:
    """Compose's ``${VAR:-default}``, nested defaults included."""
    while (match := VAR.search(value)) is not None:
        name, default = match.group(1), match.group(2) or ""
        value = value[: match.start()] + env.get(name, default) + value[match.end() :]
    return value


def _compose_images(env: dict[str, str]) -> dict[str, str]:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    return {name: _interpolate(svc["image"], env) for name, svc in services.items() if "image" in svc}


def test_ag1_the_documented_compose_settings_send_every_pipeshub_image_to_the_mirror() -> None:
    env = {
        "PIPESHUB_IMAGE": f"{MIRROR}/pipeshubai/pipeshub-ai",
        "IMAGE_TAG": "0.11.0",
        "SANDBOX_DOCKER_IMAGE": f"{MIRROR}/pipeshubai/pipeshub-sandbox:0.11.0",
    }
    images = _compose_images(env)
    for service in ("pipeshub-ai", "sandbox-broker", "egress-proxy", "sandbox-image"):
        assert images[service].startswith(f"{MIRROR}/"), (service, images[service])
    # The app's own environment names the same sandbox image the broker is told to run.
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    broker_env = services["sandbox-broker"]["environment"]
    assert _interpolate(str(broker_env["SANDBOX_DOCKER_IMAGE"]), env) == env["SANDBOX_DOCKER_IMAGE"]


def test_ag1_b_the_store_image_table_is_every_compose_image_without_a_registry_setting() -> None:
    images = _compose_images({})
    unset = {name: image for name, image in _compose_images({"PIPESHUB_IMAGE": "x", "SANDBOX_DOCKER_IMAGE": "x"}).items()
             if image != "x" and not image.startswith("x:")}
    documented = {row[0].strip("`"): row[1].split("`")[1] for row in _table("the names the file uses")}
    assert documented == unset, f"the guide's table differs from {COMPOSE.name}: {sorted(set(documented.items()) ^ set(unset.items()))}"
    assert all(images[name] == image for name, image in documented.items())


def _values_block() -> str:
    helm = _guide().split("### Helm", 1)[1].split("### ", 1)[0]
    block = re.search(r"```yaml\n(.*?)```", helm, re.S)
    assert block is not None, "the Helm section has no values block"
    return block.group(1)


@pytest.mark.skipif(shutil.which("helm") is None, reason="needs the helm binary (deployment/helm/tests/check_chart.sh)")
@pytest.mark.parametrize(
    "variant",
    [
        ["-f", "values-local.yaml", "--set", "config.sandboxMode=broker", "--set", "sandbox.dind.enabled=true"],
        ["-f", "values-local.yaml", "--set", "neo4j.enabled=false", "--set", "arango.enabled=true",
         "--set", "arango.auth.rootPassword=ci", "--set", "etcd.enabled=true", "--set", "config.kvStoreType=etcd"],
    ],
    ids=["broker", "arango-etcd"],
)
def test_ag2_the_chart_rendered_with_the_guides_values_names_only_mirror_images(tmp_path: Path, variant: list[str]) -> None:
    shutil.copytree(CHART, tmp_path / "helm")
    chart = tmp_path / "helm" / "pipeshub-ai"
    values = tmp_path / "mirror.yaml"
    values.write_text(_values_block(), encoding="utf-8")
    secrets = [
        "--set", "secretKey=ci", "--set", "mongodb.auth.rootPassword=ci", "--set", "redis.auth.password=ci",
        "--set", "neo4j.auth.password=ci", "--set", "mongodb.auth.usernames[0]=p", "--set", "mongodb.auth.passwords[0]=p",
        "--set", "mongodb.auth.databases[0]=p",
        # The chart refuses to render without a real Qdrant key (main #3893).
        "--set", "qdrant.apiKey=ci",
    ]
    subprocess.run(["helm", "dependency", "build", "."], cwd=chart, check=True, capture_output=True, timeout=120)
    result = subprocess.run(
        ["helm", "template", "t", ".", *variant, *secrets, "-f", str(values)],
        cwd=chart, check=False, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    images = re.findall(r"^\s+image:\s*\"?([^\"\s]+)\"?\s*$", rendered, re.M)
    sandbox = re.findall(r"name: SANDBOX_DOCKER_IMAGE\s+value:\s*\"?([^\"\s]+)", rendered)
    assert images and sandbox
    foreign = sorted({i for i in images + sandbox if not i.startswith(f"{MIRROR}/")})
    assert not foreign, f"images not from the mirror: {foreign}"


def _allowlist_images() -> dict[str, str]:
    from harness_engines.release import release_allowlist

    return {r.version.kind: r.image for r in release_allowlist()}


def test_ag3_the_engine_image_table_is_the_release_allowlist() -> None:
    documented = {row[1].strip("`") for row in _table("### Coding-engine images")}
    assert documented == set(_allowlist_images().values())


@dataclass
class _Registry:
    looked_up: list[str] = field(default_factory=list[str])
    pulled: list[str] = field(default_factory=list[str])

    def get_registry_data(self, image: str) -> object:
        self.looked_up.append(image)
        raise RuntimeError("no route to registry")

    def pull(self, image: str) -> None:
        self.pulled.append(image)
        raise RuntimeError("no route to registry")


@dataclass
class _OfflineDaemon:
    """A Docker daemon with no route out, holding the images that were loaded onto it."""

    loaded: set[str]
    images: _Registry = field(default_factory=_Registry)

    @property
    def client(self) -> _OfflineDaemon:
        return self

    async def ensure_image(self, image: str) -> bool:
        return image in self.loaded

    async def image_digests(self, image: str) -> list[str]:
        if image not in self.loaded:
            raise LookupError(image)
        return ["sha256:" + "0" * 64]

    async def pull_image(self, image: str) -> None:
        self.images.pull(image)

    async def run_blocking(self, fn: Callable[..., object], *args: object) -> object:
        return fn(*args)


def _availability(daemon: _OfflineDaemon) -> object:
    from app.harness.sandbox.engine_images import EngineImageAvailability
    from app.sandbox_broker_main import engine_images
    from harness_core.testing.clock import ManualClock

    return EngineImageAvailability(daemon, engine_images(), ManualClock())  # type: ignore[arg-type]


def test_ag3_b_engines_loaded_under_their_names_run_offline_with_no_registry_call() -> None:
    daemon = _OfflineDaemon(loaded=set(_allowlist_images().values()))
    availability = _availability(daemon)
    assert asyncio.run(availability.unavailable()) == ()  # type: ignore[attr-defined]
    assert asyncio.run(availability.prepull()) == ()  # type: ignore[attr-defined]
    assert daemon.images.looked_up == [] and daemon.images.pulled == []


def test_ag3_c_a_copy_under_a_mirror_name_is_not_used_and_its_engine_is_unavailable() -> None:
    images = _allowlist_images()
    codex = images["codex"]
    daemon = _OfflineDaemon(loaded={i for i in images.values() if i != codex} | {f"{MIRROR}/{codex}"})
    unavailable = asyncio.run(_availability(daemon).unavailable())  # type: ignore[attr-defined]
    assert [ref for ref in unavailable if ref.startswith("codex@")], unavailable
    assert not [ref for ref in unavailable if ref.startswith(("opencode@", "claude_code@"))], unavailable
    assert daemon.images.looked_up == [codex]


def test_ag4_the_offline_and_no_pull_settings_reach_what_reads_them() -> None:
    app_env = [str(e) for e in yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["pipeshub-ai"]["environment"]]
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "TELEMETRY_COLLECTION_FLAG"):
        assert f"{name}=${{{name}:-}}" in app_env, name
        assert f"`{name}" in _guide(), name
    installer = (REPO_ROOT / "deployment/docker-compose/install.sh").read_text(encoding="utf-8")
    assert "--no-pull)" in installer and "PIPESHUB_NO_PULL" in installer
    assert re.search(r'APP_IMAGE_REPO="\$\{PIPESHUB_IMAGE:-pipeshubai/pipeshub-ai\}"', installer)
    # The installer's own no-pull logic is exercised by deployment/docker-compose/tests/installer_test.sh.
    assert "should_pull_image" in (REPO_ROOT / "deployment/docker-compose/tests/installer_test.sh").read_text(encoding="utf-8")

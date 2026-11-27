"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

The operator's deployment settings (ADR-OPS-01 D9) reach what reads them, in
both shipped configurations.

- OPS-08: AGENT_HARNESS_KILL_SWITCH reaches the app and the egress proxy in
  Compose and the app in Helm (agentHarness.killSwitch);
  HARNESS_ENGINE_IMAGE_REGISTRY reaches the worker and the broker in Compose
  and Helm, where it defaults to global.imageRegistry.
- OPS-09: global.imageRegistry alone sends every image the chart renders to
  the mirror, and a reference that names its own registry keeps it.

The Compose checks read the file; the Helm checks render the chart (skipped
when helm is not on PATH).
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

import yaml

from test_node_logging import CHART

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose" / "docker-compose.yml"
MIRROR = "registry.internal:5000"
APP_VERSION = yaml.safe_load((Path(CHART) / "Chart.yaml").read_text(encoding="utf-8"))["appVersion"]
BASE = [
    "--set", "secretKey=ci", "--set", "mongodb.auth.rootPassword=ci", "--set", "redis.auth.password=ci",
    "--set", "neo4j.auth.password=ci", "--set", "qdrant.apiKey=ci", "--set", "mongodb.auth.usernames[0]=p",
    "--set", "mongodb.auth.passwords[0]=p", "--set", "mongodb.auth.databases[0]=p",
]
ONLY_THE_MIRROR = ["--set", f"global.imageRegistry={MIRROR}", "--set", "global.security.allowInsecureImages=true"]
# Every store, sandbox path and sidecar the chart can render, across the variants.
VARIANTS = {
    "broker-dind": ["-f", "values-local.yaml", "--set", "config.sandboxMode=broker", "--set", "sandbox.dind.enabled=true"],
    "arango-etcd": ["-f", "values-local.yaml", "--set", "neo4j.enabled=false", "--set", "arango.enabled=true",
                    "--set", "arango.auth.rootPassword=ci", "--set", "etcd.enabled=true",
                    "--set", "config.kvStoreType=etcd"],
    "legacy-docker-dind": ["--set", "config.sandboxMode=docker", "--set", "sandbox.dind.enabled=true",
                           "--set", "persistence.accessModes={ReadWriteMany}"],
    "builtin-stores": ["-f", "values-eks.yaml", "--set", "secretManagement.existingSecrets.enabled=true",
                       "--set", "secretManagement.existingSecrets.appSecretName=s",
                       "--set", "config.frontendPublicUrl=https://p.example.com",
                       "--set", "config.allowedOrigins=https://p.example.com"],
}
IMAGE_LINE = re.compile(r"^\s+(?:-\s+)?image:\s*\"?([^\"\s]+)\"?\s*$", re.M)


def compose_env(service: str) -> dict[str, str]:
    raw = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"][service]["environment"]
    if isinstance(raw, list):
        return dict(entry.split("=", 1) for entry in raw)
    return {k: str(v) for k, v in raw.items()}


def helm(*args: str) -> str:
    return subprocess.run(
        ["helm", "template", "ci", ".", *BASE, *args],
        cwd=CHART, check=True, capture_output=True, text=True, timeout=120,
    ).stdout


def workloads(rendered: str) -> dict[str, dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    return {d["metadata"]["name"]: d for d in docs if d["kind"] in ("Deployment", "StatefulSet")}


def container_env(workload: dict[str, Any], name: str) -> dict[str, Any]:
    spec = workload["spec"]["template"]["spec"]
    container = next(c for c in spec["containers"] if c["name"] == name)
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def sandbox_images(rendered: str) -> list[str]:
    return re.findall(r"name: SANDBOX_DOCKER_IMAGE\s+value:\s*\"?([^\"\s]+)\"?", rendered)


class ComposeSettings(unittest.TestCase):
    def test_ops08_the_kill_switch_reaches_the_app_and_the_egress_proxy_off_by_default(self) -> None:
        for service in ("pipeshub-ai", "egress-proxy"):
            self.assertEqual(
                compose_env(service).get("AGENT_HARNESS_KILL_SWITCH"), "${AGENT_HARNESS_KILL_SWITCH:-false}", service,
            )

    def test_ops08_the_engine_image_registry_reaches_the_worker_and_the_broker_empty_by_default(self) -> None:
        for service in ("pipeshub-ai", "sandbox-broker"):
            self.assertEqual(
                compose_env(service).get("HARNESS_ENGINE_IMAGE_REGISTRY"), "${HARNESS_ENGINE_IMAGE_REGISTRY:-}", service,
            )

    def test_ops08_the_egress_proxy_reaches_nodes_state_api_on_the_sandbox_plane(self) -> None:
        services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
        self.assertEqual(compose_env("egress-proxy")["AGENT_HARNESS_STATE_URL"], "http://harness-state:3002")
        self.assertIn("sandbox_plane", services["egress-proxy"]["networks"])
        self.assertIn("harness-state", services["pipeshub-ai"]["networks"]["sandbox_plane"]["aliases"])


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class HelmSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["helm", "dependency", "build", "."], cwd=CHART, check=True, capture_output=True, timeout=120)

    def _app_and_broker(self, *args: str) -> tuple[dict[str, Any], dict[str, Any]]:
        found = workloads(helm(*VARIANTS["broker-dind"], *args))
        return container_env(found["ci-pipeshub-ai"], "pipeshub-ai"), container_env(
            found["ci-pipeshub-ai-sandbox-broker"], "sandbox-broker",
        )

    def test_ops08_the_kill_switch_is_a_value_off_by_default(self) -> None:
        app, _ = self._app_and_broker()
        self.assertEqual(app["AGENT_HARNESS_KILL_SWITCH"], "false")
        app, _ = self._app_and_broker("--set", "agentHarness.killSwitch=true")
        self.assertEqual(app["AGENT_HARNESS_KILL_SWITCH"], "true")

    def test_ops08_the_engine_image_registry_defaults_to_the_global_registry(self) -> None:
        for args, expected in (
            ((), ""),
            (tuple(ONLY_THE_MIRROR), MIRROR),
            ((*ONLY_THE_MIRROR, "--set", "agentHarness.engineImageRegistry=engines.internal"), "engines.internal"),
        ):
            app, broker = self._app_and_broker(*args)
            self.assertEqual(app["HARNESS_ENGINE_IMAGE_REGISTRY"], expected, args)
            self.assertEqual(broker["HARNESS_ENGINE_IMAGE_REGISTRY"], expected, args)

    def test_ops09_the_global_registry_alone_sends_every_image_to_the_mirror(self) -> None:
        for name, variant in VARIANTS.items():
            with self.subTest(variant=name):
                rendered = helm(*variant, *ONLY_THE_MIRROR)
                images, sandbox = IMAGE_LINE.findall(rendered), sandbox_images(rendered)
                self.assertTrue(images and sandbox)
                foreign = sorted({i for i in images + sandbox if not i.startswith(f"{MIRROR}/")})
                self.assertEqual(foreign, [], f"images not from the mirror: {foreign}")

    def test_ops09_the_mirror_keeps_each_images_path(self) -> None:
        rendered = helm(*VARIANTS["arango-etcd"], *ONLY_THE_MIRROR)
        images = set(IMAGE_LINE.findall(rendered))
        self.assertIn(f"{MIRROR}/coreos/etcd:v3.5.17@sha256:a055da833a7c013b836ed0822e8ec1f99b059658be255ad8d0fcd31b635ae3d6", images)
        self.assertIn(f"{MIRROR}/pipeshubai/pipeshub-sandbox:{APP_VERSION}", sandbox_images(rendered))
        self.assertIn(f"{MIRROR}/docker:27.5.1-dind@sha256:aa3df78ecf320f5fafdce71c659f1629e96e9de0968305fe1de670e0ca9176ce", set(IMAGE_LINE.findall(helm(*VARIANTS["broker-dind"], *ONLY_THE_MIRROR))))

    def test_ops09_with_no_global_registry_images_are_unchanged(self) -> None:
        rendered = helm(*VARIANTS["arango-etcd"])
        images = set(IMAGE_LINE.findall(rendered))
        self.assertIn("quay.io/coreos/etcd:v3.5.17@sha256:a055da833a7c013b836ed0822e8ec1f99b059658be255ad8d0fcd31b635ae3d6", images)
        self.assertIn("arangodb:3.12.4@sha256:b7cc8dbf5f24c0fe235b6e50a26dbcfaebaf9abe0d322acefcf3dab63b5a0102", images)
        self.assertIn(f"pipeshubai/pipeshub-sandbox:{APP_VERSION}", sandbox_images(rendered))

    def test_ops09_a_reference_naming_its_own_registry_keeps_it(self) -> None:
        rendered = helm(
            *VARIANTS["arango-etcd"], *ONLY_THE_MIRROR,
            "--set", "etcd.image.repository=other.example/etcd",
            "--set", "arango.image.repository=localhost/arangodb",
            "--set", "config.sandboxDockerImage=sandbox.example:5000/sandbox:1",
        )
        images = set(IMAGE_LINE.findall(rendered))
        self.assertIn("other.example/etcd:v3.5.17@sha256:a055da833a7c013b836ed0822e8ec1f99b059658be255ad8d0fcd31b635ae3d6", images)
        self.assertIn("localhost/arangodb:3.12.4@sha256:b7cc8dbf5f24c0fe235b6e50a26dbcfaebaf9abe0d322acefcf3dab63b5a0102", images)
        self.assertEqual(set(sandbox_images(rendered)), {"sandbox.example:5000/sandbox:1"})


if __name__ == "__main__":
    unittest.main()

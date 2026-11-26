"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

Node's logs in the app pod (D3, DP-3/DP-4). By default Node logs to stdout
only; with logging.nodeFiles.enabled its files go to an emptyDir the pod's
uid can write through fsGroup, never to a path inside the image. Renders the
chart with helm (skipped when helm is not on PATH).
"""

import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

import yaml

CHART = Path(__file__).resolve().parents[1] / "pipeshub-ai"
APP = "ci-pipeshub-ai"
SECRETS = [
    "--set", "secretKey=ci-secret-key",
    "--set", "mongodb.auth.rootPassword=ci-root",
    "--set", "redis.auth.password=ci-redis",
    "--set", "neo4j.auth.password=ci-neo4j",
    "--set", "qdrant.apiKey=ci-qdrant",
    "--set", "mongodb.auth.usernames[0]=pipeshub",
    "--set", "mongodb.auth.passwords[0]=ci-app",
    "--set", "mongodb.auth.databases[0]=pipeshub",
    "--set", "sandbox.dind.enabled=true",
    "--set", "persistence.accessModes={ReadWriteMany}",
]


def render(*args: str) -> dict[str, Any]:
    out = subprocess.run(
        ["helm", "template", "ci", str(CHART), *SECRETS, *args],
        check=True, capture_output=True, text=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    return next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == APP)


def app_container(deployment: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "pipeshub-ai")


def env(container: dict[str, Any]) -> dict[str, Any]:
    return {e["name"]: e.get("value") for e in container["env"]}


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class NodeLogging(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["helm", "dependency", "build", str(CHART)], check=True, capture_output=True)

    def test_default_is_stdout_only_with_no_log_volume(self) -> None:
        deployment = render()
        container = app_container(deployment)
        self.assertEqual(env(container)["NODE_LOG_FILES"], "false")
        self.assertNotIn("NODE_LOG_DIR", env(container))
        volumes = {v["name"] for v in deployment["spec"]["template"]["spec"]["volumes"]}
        self.assertNotIn("node-logs", volumes)

    def test_files_go_to_an_emptydir_the_pod_uid_can_write(self) -> None:
        deployment = render("--set", "logging.nodeFiles.enabled=true")
        spec = deployment["spec"]["template"]["spec"]
        container = app_container(deployment)
        values = env(container)
        self.assertEqual(values["NODE_LOG_FILES"], "true")
        self.assertEqual(values["NODE_LOG_DIR"], "/var/log/pipeshub/node")
        mount = next(m for m in container["volumeMounts"] if m["name"] == "node-logs")
        self.assertEqual(mount["mountPath"], "/var/log/pipeshub/node")
        volume = next(v for v in spec["volumes"] if v["name"] == "node-logs")
        self.assertEqual(volume, {"name": "node-logs", "emptyDir": {}})
        uid = container["securityContext"]["runAsUser"]
        self.assertEqual(spec["securityContext"]["fsGroup"], uid)

    def test_a_declared_path_is_used_as_given(self) -> None:
        deployment = render("--set", "logging.nodeFiles.enabled=true", "--set", "logging.nodeFiles.path=/logs/node")
        container = app_container(deployment)
        self.assertEqual(env(container)["NODE_LOG_DIR"], "/logs/node")
        mount = next(m for m in container["volumeMounts"] if m["name"] == "node-logs")
        self.assertEqual(mount["mountPath"], "/logs/node")


if __name__ == "__main__":
    unittest.main()

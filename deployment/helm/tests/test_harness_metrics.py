"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

R41SUPPLY-FU-13 (ADR-OPS-05): with several Node replicas, each replica's metrics are scraped on their own.

- HM-1: the process metrics port is scraped per pod (prometheus.io annotations on the pod, not the Service).
- HM-2: with metrics.harness.enabled each pod runs a Prometheus agent that scrapes its own Node's merged harness route
  on loopback, with the credential file Node writes, and labels every series with the pod; the route and the file
  are the ones Node and the Compose Prometheus use.
- HM-3: the credential stays in the pod: a memory-backed volume shared by Node and the agent only, the agent runs as
  Node's user (the file is 0600), Node's internal port stays on loopback and out of the Service.
- HM-4: the agent's image is pinned by digest and follows global.imageRegistry; the NetworkPolicy lets it reach the
  receiver it was given and nothing else new.
- HM-5: off by default, and refused without somewhere to write.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

from test_node_logging import CHART
from test_operator_settings import BASE

REPO = Path(__file__).resolve().parents[3]
DIND = ["--set", "sandbox.dind.enabled=true", "--set", "persistence.accessModes={ReadWriteMany}"]
# The read-only root and the network policy HM-3/HM-4 check are the hardened profile's (ADR-OPS-07).
ON = [*DIND, "--set", "hardened=true", "--set", "metrics.harness.enabled=true",
      "--set", "metrics.harness.remoteWrite.url=http://prometheus.monitoring:9090/api/v1/write",
      "--set", r"metrics.harness.remoteWrite.peers[0].namespaceSelector.matchLabels.kubernetes\.io/metadata\.name=monitoring"]


def render(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["helm", "template", "ci", ".", *BASE, *args], cwd=CHART, check=check,
                          capture_output=True, text=True, timeout=120)


def docs(*args: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(render(*args).stdout) if d]


def app(rendered: list[dict]) -> dict:
    (doc,) = [d for d in rendered if d["kind"] == "Deployment" and d["metadata"]["name"] == "ci-pipeshub-ai"]
    return doc


def container(deployment: dict, name: str) -> dict:
    (c,) = [c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == name]
    return c


def env(c: dict) -> dict[str, str]:
    return {e["name"]: e.get("value", "") for e in c.get("env", [])}


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class HelmHarnessMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["helm", "dependency", "build", "."], cwd=CHART, check=True, capture_output=True, timeout=120)
        cls.off = docs(*DIND)
        cls.on = docs(*ON)
        cls.deployment = app(cls.on)
        cls.pod = cls.deployment["spec"]["template"]
        cls.agent = container(cls.deployment, "harness-metrics")
        cls.node = container(cls.deployment, "pipeshub-ai")
        (cm,) = [d for d in cls.on if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "ci-pipeshub-ai-harness-metrics"]
        cls.config = yaml.safe_load(cm["data"]["agent.yml"])

    def test_hm_1_process_metrics_are_scraped_per_pod(self) -> None:
        annotations = app(self.off)["spec"]["template"]["metadata"]["annotations"]
        self.assertEqual(annotations["prometheus.io/scrape"], "true")
        self.assertEqual(annotations["prometheus.io/port"], "9090")
        self.assertEqual(annotations["prometheus.io/path"], "/metrics")
        (svc,) = [d for d in self.off if d["kind"] == "Service" and d["metadata"]["name"] == "ci-pipeshub-ai"]
        self.assertNotIn("prometheus.io/scrape", (svc["metadata"].get("annotations") or {}))

    def test_hm_2_each_pod_scrapes_its_own_node_and_labels_the_pod(self) -> None:
        (job,) = self.config["scrape_configs"]
        self.assertEqual(job["static_configs"], [{"targets": ["127.0.0.1:3002"]}])
        node_route = "/api/v1/agent-harness/internal/metrics"
        self.assertEqual(job["metrics_path"], node_route)
        compose = yaml.safe_load((REPO / "deployment/monitoring/prometheus/prometheus.yml").read_text(encoding="utf-8"))
        self.assertEqual(compose["scrape_configs"][0]["metrics_path"], node_route)
        self.assertEqual(job["authorization"]["credentials_file"], env(self.node)["HARNESS_METRICS_CREDENTIAL_FILE"])
        self.assertEqual(self.config["global"]["external_labels"]["pod"], "${POD_NAME}")
        self.assertIn("--enable-feature=expand-external-labels", self.agent["args"])
        self.assertIn("--enable-feature=agent", self.agent["args"])
        pod_env = {e["name"]: e for e in self.agent["env"]}
        self.assertEqual(pod_env["POD_NAME"]["valueFrom"]["fieldRef"]["fieldPath"], "metadata.name")
        self.assertEqual(self.config["remote_write"][0]["url"], "http://prometheus.monitoring:9090/api/v1/write")
        controller = (REPO / "backend/nodejs/apps/src/modules/agent-harness/transport/metrics-credential-file.ts")
        self.assertIn("'HARNESS_METRICS_CREDENTIAL_FILE'", controller.read_text(encoding="utf-8"))

    def test_hm_3_the_credential_never_leaves_the_pod(self) -> None:
        volumes = {v["name"]: v for v in self.pod["spec"]["volumes"]}
        self.assertEqual(volumes["harness-metrics-credential"]["emptyDir"]["medium"], "Memory")
        holders = [c["name"] for c in self.pod["spec"]["containers"]
                   if any(m["name"] == "harness-metrics-credential" for m in c.get("volumeMounts", []))]
        self.assertEqual(sorted(holders), ["harness-metrics", "pipeshub-ai"])
        (mount,) = [m for m in self.agent["volumeMounts"] if m["name"] == "harness-metrics-credential"]
        self.assertTrue(mount["readOnly"])
        uid = self.node["securityContext"]["runAsUser"]
        self.assertEqual(self.agent["securityContext"]["runAsUser"], uid)
        self.assertEqual(env(self.node)["HARNESS_METRICS_CREDENTIAL_OWNER"], str(uid))
        self.assertEqual(env(self.node)["AGENT_HARNESS_INTERNAL_HOST"], "127.0.0.1")
        self.assertTrue(self.agent["securityContext"]["readOnlyRootFilesystem"])
        self.assertIn("--web.listen-address=127.0.0.1:9094", self.agent["args"])
        for svc in (d for d in self.on if d["kind"] == "Service"):
            self.assertNotIn(3002, [p["port"] for p in svc["spec"]["ports"]], svc["metadata"]["name"])

    def test_hm_4_the_agent_image_is_pinned_and_mirrored(self) -> None:
        self.assertRegex(self.agent["image"], r"^prom/prometheus:v[\d.]+@sha256:[0-9a-f]{64}$")
        mirrored = app(docs(*ON, "--set", "global.imageRegistry=mirror.example",
                            "--set", "global.security.allowInsecureImages=true"))
        self.assertTrue(container(mirrored, "harness-metrics")["image"].startswith("mirror.example/"))
        (policy,) = [d for d in self.on if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "ci-pipeshub-ai"]
        rules = [r for r in policy["spec"]["egress"]
                 if any(p.get("namespaceSelector", {}).get("matchLabels", {}).get("kubernetes.io/metadata.name")
                        == "monitoring" for p in r.get("to", []))]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["ports"], [{"protocol": "TCP", "port": 9090}])

    def test_hm_5_off_by_default_and_refused_without_a_receiver(self) -> None:
        names = [c["name"] for c in app(self.off)["spec"]["template"]["spec"]["containers"]]
        self.assertNotIn("harness-metrics", names)
        self.assertNotIn("HARNESS_METRICS_CREDENTIAL_FILE", env(container(app(self.off), "pipeshub-ai")))
        refused = render(*DIND, "--set", "metrics.harness.enabled=true", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertRegex(refused.stderr, re.escape("needs metrics.harness.remoteWrite.url"))


if __name__ == "__main__":
    unittest.main()

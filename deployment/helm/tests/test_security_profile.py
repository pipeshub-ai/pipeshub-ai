"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

The security profile in the chart (ADR-OPS-07, R51-COMPAT): `hardened` decides every default that would break a
release upgraded from a chart without it.

- PR-1: default values render the compatibility set: no NetworkPolicy, a writable root for the app, the docker
  sandbox, Kafka PLAINTEXT with no login in the app, MongoDB's entrypoint starting as root, Qdrant as root, and
  PIPESHUB_HARDENED=false in the app.
- PR-2: hardened=true renders the hardened set.
- PR-3: an explicit per-item value wins over `hardened`, in both directions.
- PR-4: NOTES names the compatibility profile and how to leave it; a hardened release is not told.
- PR-5: against the chart this branch started from (the merge base with origin/main), the compatibility render keeps
  the fields an upgrade would otherwise change: MongoDB's and Qdrant's identities, Kafka's listener protocol, the
  app's root filesystem, its sandbox mode and the chart's own network policies. Skipped without git or that commit.

The checks render the chart (skipped when helm is not on PATH).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from test_netpol import notes
from test_node_logging import CHART
from test_operator_settings import BASE

REPO = CHART.parents[2]
DIND = ["--set", "sandbox.dind.enabled=true", "--set", "persistence.accessModes={ReadWriteMany}"]
KAFKA = ["--set", "messageBroker.type=kafka"]
# The chart-owned MongoDB (the EKS preset's), not the Bitnami subchart.
BUILTIN_MONGO = ["--set", "mongodb.enabled=false", "--set", "mongodb.builtin.enabled=true"]
DEFAULT = [*DIND, *KAFKA, *BUILTIN_MONGO]
HARDENED = ["--set", "hardened=true"]


def render(chart: Path, *args: str) -> list[dict[str, Any]]:
    out = subprocess.run(["helm", "template", "ci", str(chart), *BASE, *args],
                         check=True, capture_output=True, text=True, timeout=120).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def workload(docs: list[dict[str, Any]], suffix: str) -> dict[str, Any]:
    (doc,) = [d for d in docs if d["kind"] in {"Deployment", "StatefulSet"} and d["metadata"]["name"].endswith(suffix)]
    return doc


def containers(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    spec = doc["spec"]["template"]["spec"]
    return {c["name"]: c for c in [*spec.get("initContainers", []), *spec["containers"]]}


def env(container: dict[str, Any]) -> dict[str, Any]:
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in container.get("env", [])}


def chart_policies(docs: list[dict[str, Any]]) -> list[str]:
    """The chart's own NetworkPolicies; the Bitnami subcharts' (named after the subchart) are not the chart's."""
    return sorted(d["metadata"]["name"] for d in docs
                  if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] not in {"ci-mongodb", "ci-redis"})


def profile(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """The fields the profile decides, as one comparable record."""
    app = containers(workload(docs, "-pipeshub-ai"))["pipeshub-ai"]
    mongo = workload(docs, "ci-mongodb")
    mongo_containers = containers(mongo)
    kafka = workload(docs, "-kafka")
    qdrant = workload(docs, "-qdrant")
    return {
        "policies": chart_policies(docs),
        "app_read_only_root": app["securityContext"].get("readOnlyRootFilesystem", False),
        "sandbox_mode": env(app).get("SANDBOX_MODE"),
        "app_kafka_sasl": "KAFKA_SASL_MECHANISM" in env(app),
        "kafka_protocols": env(containers(kafka)["kafka"])["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"],
        "kafka_init": sorted(c["name"] for c in kafka["spec"]["template"]["spec"].get("initContainers", [])),
        "mongo_pod": mongo["spec"]["template"]["spec"].get("securityContext", {}),
        "mongo_keyfile": mongo_containers["keyfile"].get("securityContext", {}),
        "mongo_server": mongo_containers["mongodb"].get("securityContext", {}),
        "qdrant_user": qdrant["spec"]["template"]["spec"].get("securityContext", {}).get("runAsUser"),
    }


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class HelmSecurityProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["helm", "dependency", "build", "."], cwd=CHART, check=True, capture_output=True, timeout=120)
        cls.compat = render(CHART, *DEFAULT)
        cls.hardened = render(CHART, *DEFAULT, *HARDENED)

    def test_pr1_default_values_render_the_compatibility_set(self) -> None:
        got = profile(self.compat)
        self.assertEqual(got["policies"], [])
        self.assertIs(got["app_read_only_root"], False)
        self.assertEqual(got["sandbox_mode"], "docker")
        self.assertFalse(got["app_kafka_sasl"])
        self.assertEqual(got["kafka_protocols"], "INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT")
        self.assertEqual(got["kafka_init"], [])
        self.assertNotIn("runAsUser", got["mongo_pod"])
        self.assertEqual(got["mongo_keyfile"].get("runAsUser"), 0)
        self.assertNotIn("capabilities", got["mongo_server"])
        self.assertEqual(got["qdrant_user"], 0)
        app = containers(workload(self.compat, "-pipeshub-ai"))["pipeshub-ai"]
        self.assertEqual(env(app)["PIPESHUB_HARDENED"], "false")
        self.assertNotIn("sandbox-broker", [d["metadata"]["name"].removeprefix("ci-pipeshub-ai-")
                                            for d in self.compat if d["kind"] == "Deployment"])

    def test_pr2_hardened_renders_the_hardened_set(self) -> None:
        got = profile(self.hardened)
        self.assertIn("ci-pipeshub-ai-default-deny", got["policies"])
        self.assertIs(got["app_read_only_root"], True)
        self.assertEqual(got["sandbox_mode"], "broker")
        self.assertTrue(got["app_kafka_sasl"])
        self.assertEqual(got["kafka_protocols"], "INTERNAL:SASL_PLAINTEXT,EXTERNAL:SASL_PLAINTEXT")
        self.assertEqual(got["kafka_init"], ["kafka-scram-user"])
        self.assertEqual(got["mongo_pod"].get("runAsUser"), 999)
        self.assertNotEqual(got["mongo_keyfile"].get("runAsUser"), 0)
        self.assertEqual(got["mongo_server"]["capabilities"], {"drop": ["ALL"]})
        self.assertEqual(got["qdrant_user"], 1000)
        app = containers(workload(self.hardened, "-pipeshub-ai"))["pipeshub-ai"]
        self.assertEqual(env(app)["PIPESHUB_HARDENED"], "true")
        deny = next(d for d in self.hardened if d["metadata"]["name"] == "ci-pipeshub-ai-default-deny")
        self.assertEqual(deny["spec"]["podSelector"], {})
        # A value given as text, as --set-string or a values file may give it, reads the same.
        self.assertEqual(profile(render(CHART, *DEFAULT, "--set-string", "hardened=yes"))["sandbox_mode"], "broker")

    def test_pr3_an_explicit_value_wins_over_the_profile(self) -> None:
        loosened = profile(render(CHART, *DEFAULT, *HARDENED,
                                  "--set", "networkPolicy.enabled=false",
                                  "--set", "securityContext.readOnlyRootFilesystem=false",
                                  "--set", "config.sandboxMode=docker",
                                  "--set", "kafka.auth.enabled=false",
                                  "--set", "mongodb.builtin.nonRoot=false",
                                  "--set", "qdrant.podSecurityContext.fsGroup=1000",
                                  "--set", "qdrant.podSecurityContext.runAsUser=0"))
        self.assertEqual(loosened["policies"], [])
        self.assertIs(loosened["app_read_only_root"], False)
        self.assertEqual(loosened["sandbox_mode"], "docker")
        self.assertEqual(loosened["kafka_protocols"], "INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT")
        self.assertNotIn("runAsUser", loosened["mongo_pod"])
        self.assertEqual(loosened["qdrant_user"], 0)
        tightened = profile(render(CHART, *DEFAULT,
                                   "--set", "networkPolicy.enabled=true",
                                   "--set", "securityContext.readOnlyRootFilesystem=true",
                                   "--set", "config.sandboxMode=broker",
                                   "--set", "kafka.auth.enabled=true",
                                   "--set", "mongodb.builtin.nonRoot=true"))
        self.assertIn("ci-pipeshub-ai", tightened["policies"])
        self.assertIs(tightened["app_read_only_root"], True)
        self.assertEqual(tightened["sandbox_mode"], "broker")
        self.assertEqual(tightened["kafka_protocols"], "INTERNAL:SASL_PLAINTEXT,EXTERNAL:SASL_PLAINTEXT")
        self.assertEqual(tightened["mongo_pod"].get("runAsUser"), 999)
        # namespaceDefaultDeny still follows the profile: an upgraded release that turns policies on denies only its
        # own pods, so other workloads in a shared namespace keep their traffic unless it asks for that too.
        deny = next(d for d in render(CHART, *DEFAULT, "--set", "networkPolicy.enabled=true")
                    if d["metadata"]["name"] == "ci-pipeshub-ai-default-deny")
        self.assertEqual(deny["spec"]["podSelector"], {"matchLabels": {"app.kubernetes.io/instance": "ci"}})

    def test_pr4_notes_name_the_compatibility_profile(self) -> None:
        # test_netpol's renders are hardened; a later --set wins.
        compat = notes(*DIND, "--set", "hardened=false")
        self.assertIn("COMPATIBILITY security profile", compat)
        self.assertIn("--set hardened=true", compat)
        self.assertNotIn("COMPATIBILITY security profile", notes(*DIND, *HARDENED))


def _upstream_base() -> str | None:
    """The commit this branch left origin/main at, when git and that ref are there."""
    if not shutil.which("git"):
        return None
    out = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=REPO, capture_output=True, text=True)
    return out.stdout.strip() or None


@unittest.skipUnless(shutil.which("helm") and _upstream_base(), "helm, git or origin/main is not available")
class HelmUpgradeFromUpstream(unittest.TestCase):
    def test_pr5_the_compatibility_render_keeps_what_an_upgrade_would_change(self) -> None:
        base = _upstream_base()
        assert base is not None
        with tempfile.TemporaryDirectory() as tmp:
            archive = subprocess.run(["git", "archive", base, "deployment/helm"], cwd=REPO, check=True,
                                     capture_output=True, timeout=120).stdout
            subprocess.run(["tar", "x", "-C", tmp], input=archive, check=True, timeout=120)
            upstream_chart = Path(tmp) / "deployment" / "helm" / "pipeshub-ai"
            subprocess.run(["helm", "dependency", "build", "."], cwd=upstream_chart, check=True,
                           capture_output=True, timeout=120)
            before = render(upstream_chart, *DEFAULT)
        after = profile(render(CHART, *DEFAULT))
        upstream = profile(before)
        for field in ("policies", "app_read_only_root", "sandbox_mode", "app_kafka_sasl", "kafka_protocols",
                      "kafka_init", "mongo_pod", "mongo_keyfile", "mongo_server"):
            self.assertEqual(after[field], upstream[field], field)
        # The earlier image ran Qdrant as root with no runAsUser; the -unprivileged image needs it said.
        self.assertIsNone(upstream["qdrant_user"])
        self.assertEqual(after["qdrant_user"], 0)


if __name__ == "__main__":
    unittest.main()

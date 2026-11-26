"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

Network isolation in the rendered chart (ADR-OPS-04, SEC-D-09). Renders the
default, DinD, etcd, EKS, EKS-on-kind and local variants with helm (skipped
when helm is not on PATH) and proves with netpol_check.py that every pod is
default-denied, that the allowed paths are exactly the expected ones, and that
the sandbox broker (privileged DinD, and the way out of every legacy
open-egress sandbox) reaches no metadata address, store or private range. The
bypass cases widen one rule the way an edit would and must be caught.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import netpol_check  # noqa: E402
import sandbox_topology  # noqa: E402

TESTS = Path(__file__).resolve().parent
CHART = TESTS.parent / "pipeshub-ai"
# Network isolation is the hardened profile's (ADR-OPS-07); every variant here renders it. The compatibility default
# (no policy) is ProfileDefault's below and test_security_profile.py's.
SECRETS = [
    "--set", "hardened=true",
    "--set", "secretKey=ci-secret-key-ci-secret-key-ci-secret",
    "--set", "mongodb.auth.rootPassword=ci-root",
    "--set", "redis.auth.password=ci-redis",
    "--set", "neo4j.auth.password=ci-neo4j",
    "--set", "qdrant.apiKey=ci-qdrant",
    "--set", "mongodb.auth.usernames[0]=pipeshub",
    "--set", "mongodb.auth.passwords[0]=ci-app",
    "--set", "mongodb.auth.databases[0]=pipeshub",
]
DIND = ["--set", "sandbox.dind.enabled=true", "--set", "persistence.accessModes={ReadWriteMany}"]
EKS = [
    "-f", str(CHART / "values-eks.yaml"),
    "--set", "secretManagement.existingSecrets.enabled=true",
    "--set", "secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets",
    "--set", "config.frontendPublicUrl=https://pipeshub.example.com",
    "--set", "config.allowedOrigins=https://pipeshub.example.com",
]
# The ALB targets pod IPs on the two published ports, so any source may open them (values-eks.yaml).
EKS_CHECK: dict[str, Any] = {"world_ingress": frozenset({3001, 8088}), "pod_identity": True}
VARIANTS: dict[str, tuple[list[str], dict[str, Any]]] = {
    "dind": (DIND, {}),
    "dind-etcd": ([*DIND, "--set", "etcd.enabled=true", "--set", "config.kvStoreType=etcd"], {}),
    "legacy-docker": ([*DIND, "--set", "config.sandboxMode=docker"], {}),
    "local": (["-f", str(CHART / "values-local.yaml")], {}),
    "local-arango": (["-f", str(CHART / "values-local.yaml"), "--set", "neo4j.enabled=false",
                      "--set", "arango.enabled=true", "--set", "arango.auth.rootPassword=ci-arango"], {}),
    "cloud": (["-f", str(CHART / "values-cloud.yaml"), "--set", "config.sandboxMode=e2b"], {}),
    "eks": (EKS, EKS_CHECK),
    "eks-kind": ([*EKS, "-f", str(TESTS / "values-eks-kind.yaml")], EKS_CHECK),
}


def render(*args: str) -> list[dict[str, Any]]:
    out = subprocess.run(["helm", "template", "ci", str(CHART), *SECRETS, *args],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def notes(*args: str) -> str:
    """NOTES.txt as an install prints it: helm template skips it and install --dry-run wants a cluster, so a copy of
    the chart renders it as a manifest field."""
    with tempfile.TemporaryDirectory() as tmp:
        chart = Path(tmp) / "chart"
        shutil.copytree(CHART, chart)
        text = (chart / "templates" / "NOTES.txt").read_text(encoding="utf-8")
        (chart / "templates" / "NOTES.txt").unlink()
        (chart / "templates" / "_zz_notes.tpl").write_text(
            '{{- define "zz.notes" -}}\n' + text + "\n{{- end }}\n", encoding="utf-8")
        (chart / "templates" / "zz-notes.yaml").write_text(
            'notes: {{ include "zz.notes" . | toJson }}\n', encoding="utf-8")
        out = subprocess.run(["helm", "template", "ci", str(chart), *SECRETS, *args,
                              "--show-only", "templates/zz-notes.yaml"],
                             check=True, capture_output=True, text=True).stdout
    return str(yaml.safe_load(out)["notes"])


def policy(docs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == name)


def write(docs: list[dict[str, Any]], directory: str) -> str:
    path = str(Path(directory) / "rendered.yaml")
    Path(path).write_text(yaml.safe_dump_all(docs), encoding="utf-8")
    return path


class ValuesDefault(unittest.TestCase):
    def test_no_shipped_values_file_turns_network_isolation_off_for_hardened(self) -> None:
        """Unset (null) follows `hardened`; only an operator's explicit false opts a hardened release out."""
        for name in ("values.yaml", "values-eks.yaml", "values-cloud.yaml", "values-local.yaml"):
            values = yaml.safe_load((CHART / name).read_text(encoding="utf-8")) or {}
            enabled = (values.get("networkPolicy") or {}).get("enabled")
            self.assertIn(enabled, (None, True), f"{name} turns network isolation off")

    def test_subchart_policies_that_admit_any_source_stay_off(self) -> None:
        values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
        self.assertIs(values["mongodb"]["networkPolicy"]["enabled"], False)
        self.assertIs(values["redis"]["networkPolicy"]["enabled"], False)


class Bypass(unittest.TestCase):
    """Widening that netpol_check.widening must flag, on synthetic rules (no helm needed)."""

    @staticmethod
    def _policy(rule: dict[str, Any], key: str = "egress") -> dict[str, Any]:
        return {"kind": "NetworkPolicy", "metadata": {"name": "p"},
                "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/component": "sandbox-broker"}},
                         "policyTypes": ["Ingress", "Egress"], key: [rule]}}

    def assertFlags(self, rule: dict[str, Any], expect: str, key: str = "egress") -> None:  # noqa: N802
        found = netpol_check.widening([self._policy(rule, key)])
        self.assertTrue(any(expect in f for f in found), f"{rule} not flagged ({found})")

    def test_any_pod(self) -> None:
        self.assertFlags({"to": [{"podSelector": {}}]}, "every pod in the namespace")

    def test_any_namespace(self) -> None:
        self.assertFlags({"to": [{"namespaceSelector": {}}]}, "every namespace")
        self.assertFlags({"from": [{"namespaceSelector": {}, "podSelector": {}}]}, "every namespace", "ingress")

    def test_no_peers(self) -> None:
        self.assertFlags({"ports": [{"protocol": "TCP", "port": 443}]}, "any peer")
        self.assertFlags({}, "any peer", "ingress")

    def test_whole_family_without_metadata_except(self) -> None:
        self.assertFlags({"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}, "cloud metadata")
        self.assertFlags({"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8"]}}]}, "cloud metadata")
        self.assertFlags({"to": [{"ipBlock": {"cidr": "128.0.0.0/1"}}]}, "cloud metadata")
        self.assertFlags({"to": [{"ipBlock": {"cidr": "::/0", "except": ["fc00::/8"]}}]}, "cloud metadata")
        self.assertFlags({"to": [{"ipBlock": {"cidr": "169.254.169.254/32"}}]}, "cloud metadata")

    def test_metadata_excepted_is_not_flagged(self) -> None:
        rule = {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["169.254.0.0/16"]}}],
                "ports": [{"protocol": "TCP", "port": 443}]}
        self.assertEqual(netpol_check.widening([self._policy(rule)]), [])

    def test_broker_rules_that_reach_private_ranges(self) -> None:
        for block in ({"cidr": "0.0.0.0/0", "except": ["169.254.0.0/16"]},
                      {"cidr": "100.64.0.0/10"}, {"cidr": "::/0", "except": ["fe80::/10"]}):
            spec = self._policy({"to": [{"ipBlock": block}], "ports": [{"protocol": "TCP", "port": 443}]})
            self.assertTrue(sandbox_topology.broker_egress_problems(spec), block)


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class Rendered(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (CHART / "charts").is_dir():
            subprocess.run(["helm", "dependency", "build", str(CHART)], check=True, capture_output=True)

    def test_every_variant_allows_exactly_the_expected_paths(self) -> None:
        for variant, (args, options) in VARIANTS.items():
            with self.subTest(variant=variant):
                self.assertEqual(netpol_check.check_docs(render(*args), **options), [])

    def test_broker_egress_is_dns_kv_and_public_443(self) -> None:
        for variant in ("dind", "dind-etcd", "eks", "eks-kind"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                args = VARIANTS[variant][0]
                self.assertEqual(sandbox_topology.check(write(render(*args), tmp), "broker", True), [])

    def test_the_broker_reaches_no_metadata_store_or_private_address(self) -> None:
        w = netpol_check.world(render(*DIND))
        broker = next(p for p in w.pods if p.role == "broker")
        reached = {(dst, port) for src, dst, port, _ in netpol_check.paths(w) if src == broker.name}
        for dst, port in reached:
            target = next(e for e in w.everything if e.name == dst)
            self.assertIn(target.role, {"dns", "public", "store:redis"}, (dst, port))
            if target.role == "public":
                self.assertEqual(port, 443)
            if target.role == "store:redis":
                self.assertEqual(port, 6379)
        self.assertNotIn("metadata", {next(e for e in w.everything if e.name == d).role for d, _ in reached})

    def test_a_widened_rule_is_caught_in_the_rendered_chart(self) -> None:
        docs = render(*DIND)
        widen: list[tuple[str, Any]] = [
            ("every pod", {"to": [{"podSelector": {}}]}),
            ("every namespace", {"to": [{"namespaceSelector": {}}]}),
            ("whole internet", {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}),
            ("any peer", {"ports": [{"protocol": "TCP", "port": 6379}]}),
        ]
        for label, rule in widen:
            with self.subTest(label=label):
                mutated = copy.deepcopy(docs)
                policy(mutated, "ci-pipeshub-ai-sandbox-broker")["spec"]["egress"].append(rule)
                self.assertNotEqual(netpol_check.check_docs(mutated), [])

    def test_dropping_the_broker_egress_type_reopens_metadata(self) -> None:
        docs = render(*DIND)
        policy(docs, "ci-pipeshub-ai-sandbox-broker")["spec"]["policyTypes"] = ["Ingress"]
        docs = [d for d in docs if d["metadata"]["name"] != "ci-pipeshub-ai-default-deny"]
        found = netpol_check.check_docs(docs)
        self.assertTrue(any("sandbox-broker -> metadata:80" in f for f in found), found)

    def test_a_store_policy_that_admits_the_broker_is_caught(self) -> None:
        docs = render(*DIND)
        rule = policy(docs, "ci-pipeshub-ai-qdrant")["spec"]["ingress"][1]
        rule["from"].append({"podSelector": {"matchLabels": {"app.kubernetes.io/component": "sandbox-broker"}}})
        policy(docs, "ci-pipeshub-ai-sandbox-broker")["spec"]["egress"].append(
            {"to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/component": "qdrant"}}}],
             "ports": [{"protocol": "TCP", "port": 6333}]})
        self.assertIn("allowed but not expected: Deployment/ci-pipeshub-ai-sandbox-broker -> "
                      "StatefulSet/ci-pipeshub-ai-qdrant:6333/TCP", netpol_check.check_docs(docs))

    def test_cluster_cidrs_are_excepted_from_both_internet_rules(self) -> None:
        docs = render(*DIND, "--set", "networkPolicy.clusterCidrs={10.96.0.0/12,fd00:10::/56}")
        for name in ("ci-pipeshub-ai", "ci-pipeshub-ai-sandbox-broker"):
            blocks = [p["ipBlock"] for r in policy(docs, name)["spec"]["egress"] for p in r.get("to") or []
                      if "ipBlock" in p and p["ipBlock"]["cidr"] in ("0.0.0.0/0", "::/0")]
            v4 = next(b for b in blocks if b["cidr"] == "0.0.0.0/0")
            v6 = next(b for b in blocks if b["cidr"] == "::/0")
            self.assertIn("10.96.0.0/12", v4["except"], name)
            self.assertNotIn("fd00:10::/56", v4["except"], name)
            self.assertIn("fd00:10::/56", v6["except"], name)

    def test_the_package_mirror_ranges_reach_the_broker_policy(self) -> None:
        docs = render(*DIND, "--set", "config.sandboxEgressAllowCidrs=10.20.0.0/24\\, 10.30.0.5/32")
        cidrs = {p["ipBlock"]["cidr"] for r in policy(docs, "ci-pipeshub-ai-sandbox-broker")["spec"]["egress"]
                 for p in r.get("to") or [] if "ipBlock" in p}
        self.assertLessEqual({"10.20.0.0/24", "10.30.0.5/32"}, cidrs)

    def test_off_is_an_explicit_opt_out_that_notes_says_so(self) -> None:
        docs = render(*DIND, "--set", "networkPolicy.enabled=false")
        self.assertFalse([d for d in docs if d["kind"] == "NetworkPolicy"])
        text = notes(*DIND, "--set", "networkPolicy.enabled=false")
        self.assertIn("NETWORK ISOLATION IS OFF", text)
        self.assertIn("PRIVILEGED Docker daemon", text)
        self.assertNotIn("NETWORK ISOLATION IS OFF", notes(*DIND))

    def test_notes_name_the_external_kv_rule_the_broker_needs(self) -> None:
        external = ["--set", "redis.enabled=false", "--set", "redis.external.enabled=true",
                    "--set", "redis.external.host=redis.example.internal"]
        self.assertIn("networkPolicy.sandboxBroker.extraEgress", notes(*DIND, *external))
        self.assertNotIn("networkPolicy.sandboxBroker.extraEgress", notes(*DIND))

    def test_the_broker_can_have_a_node_pool_of_its_own(self) -> None:
        docs = render(*DIND, "--set", "sandbox.nodePool.nodeSelector.pipeshub/role=sandbox",
                      "--set", "sandbox.nodePool.tolerations[0].key=pipeshub/role",
                      "--set", "sandbox.nodePool.tolerations[0].operator=Exists",
                      "--set", "sandbox.nodePool.tolerations[0].effect=NoSchedule")
        pods = {d["metadata"]["name"]: d["spec"]["template"]["spec"] for d in docs if d["kind"] == "Deployment"}
        broker, app = pods["ci-pipeshub-ai-sandbox-broker"], pods["ci-pipeshub-ai"]
        self.assertEqual(broker["nodeSelector"], {"pipeshub/role": "sandbox"})
        self.assertEqual(broker["tolerations"][0]["key"], "pipeshub/role")
        self.assertNotIn("sandbox", str(app.get("nodeSelector")))


if __name__ == "__main__":
    unittest.main()

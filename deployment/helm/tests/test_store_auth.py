"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

SEC-D-06 (ADR-OPS-03) in the chart: the stores it runs that have no credential of their own take one.

- SA-1: etcd serves TLS only, with --client-cert-auth, its probes sign in with its certificate, and the peer port is
  on loopback only.
- SA-2: every etcd client (the app, the sandbox broker) uses https and mounts its own client certificate; each is
  issued by the CA etcd trusts, with its component's name, and no client holds the CA key.
- SA-3: Kafka's listeners are SASL only with SCRAM-SHA-512, an init container writes the user first, and the app reads
  the login from the same secret. The login reaches no KAFKA_-prefixed broker variable (cp-kafka writes those into
  server.properties).
- SA-4: a Kafka password given in values that would break the JAAS quoting is refused.

The checks render the chart (skipped when helm or openssl is not on PATH).
"""

import base64
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from test_node_logging import CHART
from test_operator_settings import BASE

# Kafka's login follows `hardened` (ADR-OPS-07); the compatibility default is test_security_profile.py's.
STORES = [
    "--set", "hardened=true",
    "-f", "values-local.yaml", "--set", "qdrant.apiKey=ci", "--set", "etcd.enabled=true",
    "--set", "config.kvStoreType=etcd", "--set", "messageBroker.type=kafka",
    "--set", "config.sandboxMode=broker", "--set", "sandbox.dind.enabled=true",
]


def helm(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "ci", ".", *BASE, *STORES, *args],
        cwd=CHART, check=check, capture_output=True, text=True, timeout=120,
    )


def _containers(doc: dict) -> list[dict]:
    spec = doc["spec"]["template"]["spec"]
    return [*spec.get("initContainers", []), *spec["containers"]]


@unittest.skipUnless(shutil.which("helm") and shutil.which("openssl"), "helm or openssl is not on PATH")
class HelmStoreAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["helm", "dependency", "build", "."], cwd=CHART, check=True, capture_output=True, timeout=120)
        cls.docs = [d for d in yaml.safe_load_all(helm().stdout) if d]
        cls.by_name = {(d["kind"], d["metadata"]["name"]): d for d in cls.docs}

    def _workload(self, suffix: str) -> dict:
        (doc,) = [d for d in self.docs if d["kind"] in {"Deployment", "StatefulSet"} and d["metadata"]["name"].endswith(suffix)]
        return doc

    def _secret(self, suffix: str) -> dict[str, str]:
        (doc,) = [d for d in self.docs if d["kind"] == "Secret" and d["metadata"]["name"].endswith(suffix)]
        return {k: base64.b64decode(v).decode() for k, v in doc["data"].items()}

    def test_sa1_etcd_serves_only_tls_clients_with_a_certificate(self) -> None:
        (etcd,) = _containers(self._workload("-etcd"))
        command = " ".join(etcd["command"])
        for flag in ("--client-cert-auth", "--trusted-ca-file /tls/ca.crt", "--cert-file /tls/tls.crt", "--key-file /tls/tls.key"):
            self.assertIn(flag, command)
        self.assertIn("--listen-client-urls https://0.0.0.0:2379", command)
        self.assertIn("--listen-peer-urls http://127.0.0.1:2380", command)
        self.assertNotIn("http://0.0.0.0", command)
        for probe in (etcd["livenessProbe"], etcd["readinessProbe"]):
            self.assertIn("--cert=/tls/tls.crt", probe["exec"]["command"])
        (service,) = [d for d in self.docs if d["kind"] == "Service" and d["metadata"]["name"].endswith("-etcd")]
        self.assertEqual([p["port"] for p in service["spec"]["ports"]], [2379], "the peer port is not a service")

    def test_sa2_each_client_uses_its_own_certificate_from_the_ca_etcd_trusts(self) -> None:
        server = self._secret("-etcd-server-tls")
        for suffix, client in (("-pipeshub-ai", "pipeshub-ai"), ("-sandbox-broker", "sandbox-broker")):
            workload = self._workload(suffix)
            (container,) = [c for c in workload["spec"]["template"]["spec"]["containers"] if c["name"] in {"pipeshub-ai", "sandbox-broker"}]
            env = {e["name"]: e.get("value") for e in container["env"]}
            self.assertTrue(env["ETCD_URL"].startswith("https://"), suffix)
            self.assertEqual(env["ETCD_CERT_FILE"], "/run/pipeshub/etcd-tls/tls.crt")
            self.assertIn("/run/pipeshub/etcd-tls", [m["mountPath"] for m in container["volumeMounts"]])
            volumes = {v["name"]: v for v in workload["spec"]["template"]["spec"]["volumes"]}
            self.assertEqual(volumes["etcd-tls"]["secret"]["secretName"], f"ci-pipeshub-ai-etcd-client-{client}")
            secret = self._secret(f"-etcd-client-{client}")
            self.assertEqual(sorted(secret), ["ca.crt", "tls.crt", "tls.key"], "no client holds the CA key")
            self.assertEqual(secret["ca.crt"], server["ca.crt"])
            with tempfile.TemporaryDirectory() as scratch:
                ca, cert = Path(scratch, "ca.crt"), Path(scratch, "tls.crt")
                ca.write_text(server["ca.crt"]); cert.write_text(secret["tls.crt"])
                verify = subprocess.run(["openssl", "verify", "-CAfile", str(ca), str(cert)], capture_output=True, text=True)
                self.assertEqual(verify.returncode, 0, verify.stderr)
                # The default name format differs between OpenSSL releases ("CN = x" on 3.0, "CN=x" later); RFC 2253 is fixed.
                subject = subprocess.run(
                    ["openssl", "x509", "-noout", "-subject", "-nameopt", "RFC2253", "-in", str(cert)], capture_output=True, text=True,
                ).stdout
                self.assertEqual(subject.strip(), f"subject=CN={client}")

    def test_sa3_kafka_takes_only_the_scram_login(self) -> None:
        kafka = self._workload("-kafka")
        init, broker = _containers(kafka)
        self.assertEqual(init["name"], "kafka-scram-user")
        self.assertIn("kafka-configs --zookeeper", init["command"][-1])
        env = {e["name"]: e for e in broker["env"]}
        self.assertEqual(env["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"]["value"], "INTERNAL:SASL_PLAINTEXT,EXTERNAL:SASL_PLAINTEXT")
        self.assertEqual(env["KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL"]["value"], "SCRAM-SHA-512")
        self.assertFalse({"KAFKA_USERNAME", "KAFKA_PASSWORD"} & set(env))
        self.assertEqual(env["PIPESHUB_KAFKA_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"], "ci-pipeshub-ai-kafka-auth")
        (app,) = [c for c in self._workload("-pipeshub-ai")["spec"]["template"]["spec"]["containers"] if c["name"] == "pipeshub-ai"]
        app_env = {e["name"]: e for e in app["env"]}
        self.assertEqual(app_env["KAFKA_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"], "ci-pipeshub-ai-kafka-auth")
        self.assertEqual(app_env["KAFKA_SASL_MECHANISM"]["value"], "scram-sha-512")
        login = self._secret("-kafka-auth")
        self.assertEqual(login["username"], "pipeshub")
        self.assertRegex(login["password"], r"^[A-Za-z0-9]{40}$")

    def test_sa4_a_password_that_breaks_the_jaas_quoting_is_refused(self) -> None:
        for bad in ('pass"word-long-enough', "short", "with space but long enough"):
            out = helm("--set-string", f"kafka.auth.password={bad}", check=False)
            self.assertNotEqual(out.returncode, 0, bad)
            self.assertIn("kafka.auth.password must be", out.stderr)
        self.assertEqual(helm("--set-string", "kafka.auth.password=Abc.def_ghi~jkl-0123", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()

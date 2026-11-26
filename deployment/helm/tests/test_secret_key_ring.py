"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

ADR-KEY-01: the second key of the SECRET_KEY ring reaches every service that reads SECRET_KEY, in both shipped
configurations, and is absent unless an operator sets it.

- KH-1: Compose passes SECRET_KEY_PREVIOUS and SECRET_KEY_NEXT, empty by default, wherever it passes SECRET_KEY
  (the app). ADR-KEY-02: the sandbox broker and the egress proxy are passed none of the ring, only their own key.
- KH-2: Helm puts secretKeyPrevious / secretKeyNext in the app secret only when set, and every container that reads
  secret-key reads them with an optional reference, so an existing or external secret without them still renders.
- KH-3: Helm refuses both at once.
- KH-4 (R38IDENT-FU-8, ADR-KEY-02 D8): by default the app reads SECRET_KEY from a file in a Secret volume (mode 0400),
  never from its environment; `secretKeyAsFile=false` goes back to the variable. The broker gets neither.

The Helm checks render the chart (skipped when helm is not on PATH).
"""

import shutil
import subprocess
import unittest

import yaml

from test_operator_settings import BASE, COMPOSE, compose_env
from test_node_logging import CHART

RING = ("SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT")
BROKER = [
    "-f", "values-local.yaml", "--set", "config.sandboxMode=broker", "--set", "sandbox.dind.enabled=true",
    "--set", "qdrant.apiKey=ci",
]


def helm(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "ci", ".", *BASE, *BROKER, *args],
        cwd=CHART, check=check, capture_output=True, text=True, timeout=120,
    )


class ComposeRing(unittest.TestCase):
    def test_kh1_every_service_given_secret_key_is_given_the_ring_empty_by_default(self) -> None:
        services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
        given = [name for name, spec in services.items() if "environment" in spec and "SECRET_KEY" in compose_env(name)]
        self.assertIn("pipeshub-ai", given)
        self.assertFalse({"sandbox-broker", "egress-proxy"} & set(given), given)
        for component, key in (("sandbox-broker", "SANDBOX_BROKER_COMPONENT_KEY"), ("egress-proxy", "EGRESS_PROXY_COMPONENT_KEY")):
            env = compose_env(component)
            self.assertEqual(env.get("PIPESHUB_COMPONENT_KEY"), f"${{{key}:-}}", component)
            self.assertFalse({"SECRET_KEY", "REDIS_PASSWORD", *RING} & set(env), component)
            self.assertEqual(compose_env("pipeshub-ai").get(key), f"${{{key}:-}}")
        for service in given:
            env = compose_env(service)
            for name in RING:
                self.assertEqual(env.get(name), f"${{{name}:-}}", f"{service} {name}")


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class HelmRing(unittest.TestCase):
    def _docs(self, *args: str) -> list[dict]:
        return [d for d in yaml.safe_load_all(helm(*args).stdout) if d]

    def test_kh2_the_ring_is_read_by_every_container_that_reads_secret_key_and_stored_only_when_set(self) -> None:
        for values, stored in (([], set()), (["--set", "secretKeyPrevious=old"], {"secret-key-previous"})):
            docs = self._docs(*values)
            secret = next(d for d in docs if d["kind"] == "Secret" and "secret-key" in d.get("data", {}))
            self.assertEqual({k for k in secret["data"] if k.startswith("secret-key-")}, stored)
            readers = 0
            for doc in docs:
                if doc["kind"] not in ("Deployment", "StatefulSet"):
                    continue
                for container in doc["spec"]["template"]["spec"]["containers"]:
                    refs = {
                        e["name"]: e["valueFrom"]["secretKeyRef"]
                        for e in container.get("env", [])
                        if "secretKeyRef" in e.get("valueFrom", {})
                    }
                    names = {e["name"] for e in container.get("env", [])}
                    if refs.get("SECRET_KEY", {}).get("key") != "secret-key" and "SECRET_KEY_FILE" not in names:
                        continue
                    readers += 1
                    for name, key in (("SECRET_KEY_PREVIOUS", "secret-key-previous"), ("SECRET_KEY_NEXT", "secret-key-next")):
                        self.assertEqual(refs[name]["key"], key, doc["metadata"]["name"])
                        self.assertIs(refs[name]["optional"], True)
            self.assertGreaterEqual(readers, 1, "the app reads secret-key")
            broker = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("sandbox-broker"))
            for container in broker["spec"]["template"]["spec"]["containers"]:
                names = {e["name"] for e in container.get("env", [])}
                self.assertFalse({"SECRET_KEY", "SECRET_KEY_FILE", "REDIS_PASSWORD", "REDIS_URL", *RING} & names, container["name"])
            self.assertFalse(
                [v for v in broker["spec"]["template"]["spec"].get("volumes", []) if v.get("name") == "secret-key"]
            )
            self.assertIn("sandbox-broker-component-key", secret["data"])

    def test_kh3_both_at_once_is_refused(self) -> None:
        both = helm("--set", "secretKeyPrevious=old", "--set", "secretKeyNext=new", check=False)
        self.assertNotEqual(both.returncode, 0)
        self.assertIn("never both", both.stderr)

    def _app(self, *args: str) -> tuple[dict, dict]:
        docs = self._docs(*args)
        app = next(
            d for d in docs
            if d["kind"] == "Deployment" and d["metadata"]["name"] == "ci-pipeshub-ai"
        )
        pod = app["spec"]["template"]["spec"]
        return pod, next(c for c in pod["containers"] if c["name"] == "pipeshub-ai")

    def test_kh4_the_root_key_is_a_0400_file_by_default_and_a_variable_on_request(self) -> None:
        pod, app = self._app()
        env = {e["name"]: e for e in app.get("env", [])}
        self.assertNotIn("SECRET_KEY", env)
        path = env["SECRET_KEY_FILE"]["value"]
        mount = next(m for m in app["volumeMounts"] if m["name"] == "secret-key")
        self.assertTrue(mount["readOnly"])
        self.assertEqual(f"{mount['mountPath']}/secret-key", path)
        volume = next(v for v in pod["volumes"] if v["name"] == "secret-key")["secret"]
        self.assertEqual(volume["secretName"], "ci-pipeshub-ai-secrets")
        self.assertEqual(volume["defaultMode"], 0o400)
        self.assertEqual(volume["items"], [{"key": "secret-key", "path": "secret-key"}])

        pod, app = self._app("--set", "secretKeyAsFile=false")
        env = {e["name"]: e for e in app.get("env", [])}
        self.assertNotIn("SECRET_KEY_FILE", env)
        self.assertEqual(env["SECRET_KEY"]["valueFrom"]["secretKeyRef"]["key"], "secret-key")
        self.assertFalse([v for v in pod["volumes"] if v["name"] == "secret-key"])


if __name__ == "__main__":
    unittest.main()

"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

R41OPS-FU-4: the chart reissues its etcd certificates before they expire, not only when one is missing.

- ER-1: the decision (`pipeshub-ai.etcdTls.decide`, driven here through a scratch chart since `helm template` cannot
  look up Secrets): a full set with more than a year left is kept; one within a year of expiry, one without a
  readable date, or a missing one reissues the whole set, dated five years from now.
- ER-2: each issued Secret records its certificate's end date, which is the certificate's own notAfter day.
- ER-3: every pod that holds an etcd certificate or trusts its CA (etcd, the app, the sandbox broker) carries the
  same date, so a reissue restarts them onto the new set together; without etcd the app pod has no such annotation.
"""

import base64
import datetime as dt
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from test_node_logging import CHART
from test_operator_settings import BASE

DAY = 86400
NOW = int(dt.datetime(2027, 1, 1, tzinfo=dt.UTC).timestamp())
ETCD = ["-f", "values-local.yaml", "--set", "etcd.enabled=true", "--set", "config.kvStoreType=etcd",
        "--set", "config.sandboxMode=broker", "--set", "sandbox.dind.enabled=true"]


def day(offset_days: int) -> str:
    return dt.datetime.fromtimestamp(NOW + offset_days * DAY, tz=dt.UTC).strftime("%Y-%m-%d")


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class EtcdTlsDecision(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="pxh-etcd-tls-")
        chart = Path(cls._tmp.name)
        (chart / "templates").mkdir()
        (chart / "Chart.yaml").write_text("apiVersion: v2\nname: probe\nversion: 0.0.0\n", encoding="utf-8")
        shutil.copy(CHART / "templates" / "_etcd_tls.tpl", chart / "templates" / "_etcd_tls.tpl")
        (chart / "templates" / "probe.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: probe\ndata:\n  decision: |\n"
            '    {{- include "pipeshub-ai.etcdTls.decide" (dict "kept" .Values.kept "expected" 3 "now" '
            "(.Values.now | int64)) | nindent 4 }}\n",
            encoding="utf-8")
        cls.chart = chart

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def decide(self, kept: list[dict]) -> dict:
        out = subprocess.run(["helm", "template", "p", str(self.chart), "--set-json", f"kept={json.dumps(kept)}",
                              "--set", f"now={NOW}"], check=True, capture_output=True, text=True, timeout=60).stdout
        (doc,) = [d for d in yaml.safe_load_all(out) if d]
        return yaml.safe_load(doc["data"]["decision"])

    def test_er_1_a_current_set_is_kept_until_a_year_before_expiry(self) -> None:
        kept = [{"crt": "x", "notAfter": day(800)}, {"crt": "x", "notAfter": day(400)}, {"crt": "x", "notAfter": day(900)}]
        self.assertEqual(self.decide(kept), {"keep": True, "notAfter": day(400)})

    def test_er_1_one_within_a_year_reissues_the_set(self) -> None:
        for close in (364, 365, 0, -30):
            with self.subTest(days_left=close):
                kept = [{"crt": "x", "notAfter": day(800)}, {"crt": "x", "notAfter": day(close)},
                        {"crt": "x", "notAfter": day(900)}]
                self.assertEqual(self.decide(kept), {"keep": False, "notAfter": day(1825)})

    def test_er_1_an_undated_unreadable_or_missing_certificate_reissues_the_set(self) -> None:
        good = {"crt": "x", "notAfter": day(800)}
        for kept in ([good, good, {"crt": "x", "notAfter": ""}],
                     [good, good, {"crt": "x", "notAfter": "next year"}],
                     [good, good, {"crt": "", "notAfter": day(800)}],
                     [good, good],
                     []):
            with self.subTest(kept=kept):
                self.assertEqual(self.decide(kept), {"keep": False, "notAfter": day(1825)})


@unittest.skipUnless(shutil.which("helm") and shutil.which("openssl"), "helm or openssl is not on PATH")
class EtcdTlsRendered(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["helm", "dependency", "build", "."], cwd=CHART, check=True, capture_output=True, timeout=120)

        def render(*args: str) -> list[dict]:
            out = subprocess.run(["helm", "template", "ci", ".", *BASE, *args], cwd=CHART, check=True,
                                 capture_output=True, text=True, timeout=120).stdout
            return [d for d in yaml.safe_load_all(out) if d]

        cls.docs = render(*ETCD)
        cls.plain = render("-f", "values-local.yaml")

    def test_er_2_each_secret_records_its_certificates_end_date(self) -> None:
        secrets = [d for d in self.docs if d["kind"] == "Secret" and "-etcd-" in d["metadata"]["name"]]
        self.assertEqual(len(secrets), 3)
        for secret in secrets:
            pem = base64.b64decode(secret["data"]["tls.crt"])
            end = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-dateopt", "iso_8601"], input=pem,
                                 check=True, capture_output=True, timeout=30).stdout.decode().split("=", 1)[1]
            self.assertEqual(secret["metadata"]["annotations"]["pipeshub.ai/tls-not-after"], end[:10],
                             secret["metadata"]["name"])
            self.assertEqual(end[:10], (dt.datetime.now(dt.UTC) + dt.timedelta(days=1825)).strftime("%Y-%m-%d"))

    def test_er_3_every_holder_restarts_onto_a_reissued_set(self) -> None:
        (secret,) = [d for d in self.docs if d["kind"] == "Secret" and d["metadata"]["name"].endswith("-etcd-server-tls")]
        date = secret["metadata"]["annotations"]["pipeshub.ai/tls-not-after"]
        for suffix in ("-etcd", "-pipeshub-ai", "-sandbox-broker"):
            (pod,) = [d["spec"]["template"] for d in self.docs
                      if d["kind"] == "Deployment" and d["metadata"]["name"].endswith(suffix)]
            self.assertEqual(pod["metadata"]["annotations"]["pipeshub.ai/etcd-tls-not-after"], date, suffix)
        (app,) = [d for d in self.plain if d["kind"] == "Deployment" and d["metadata"]["name"] == "ci-pipeshub-ai"]
        self.assertNotIn("pipeshub.ai/etcd-tls-not-after", app["spec"]["template"]["metadata"].get("annotations", {}))


if __name__ == "__main__":
    unittest.main()

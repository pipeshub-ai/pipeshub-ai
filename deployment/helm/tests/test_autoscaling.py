"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

The app's HPA scales on a signal one Node process reaches (PERF-E). Each pod
runs one Node, which serves the API on one thread: saturated, it is one busy
core, so a target of 70 % of a multi-core request never fired. The CPU target
is absolute (one core), the event-loop utilization Node serves on the metrics
port is the opt-in direct signal, and Compose is untouched.
"""

import shutil
import subprocess
import unittest
from typing import Any

import yaml

from test_node_logging import CHART, SECRETS, app_container, env, render


def render_hpa(*args: str) -> dict[str, Any] | None:
    out = subprocess.run(
        ["helm", "template", "ci", str(CHART), *SECRETS, *args],
        check=True, capture_output=True, text=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    return next((d for d in docs if d["kind"] == "HorizontalPodAutoscaler"), None)


def metric(hpa: dict[str, Any], kind: str, name: str) -> dict[str, Any] | None:
    for m in hpa["spec"]["metrics"]:
        if m["type"] == kind and (m.get("resource") or m.get("pods", {}).get("metric", {})).get("name") == name:
            return m
    return None


@unittest.skipUnless(shutil.which("helm"), "helm is not on PATH")
class Autoscaling(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["helm", "dependency", "build", str(CHART)], check=True, capture_output=True)

    def test_default_cpu_target_is_one_core_not_a_share_of_the_request(self) -> None:
        hpa = render_hpa()
        assert hpa is not None
        cpu = metric(hpa, "Resource", "cpu")
        assert cpu is not None
        self.assertEqual(cpu["resource"]["target"], {"type": "AverageValue", "averageValue": "700m"})
        self.assertIsNone(metric(hpa, "Pods", "pipeshub_nodejs_event_loop_utilization"))

    def test_presets_keep_the_one_core_target(self) -> None:
        for preset in ("values-eks.yaml", "values-cloud.yaml"):
            with self.subTest(preset=preset):
                hpa = render_hpa("-f", str(CHART / preset))
                assert hpa is not None
                cpu = metric(hpa, "Resource", "cpu")
                assert cpu is not None
                self.assertEqual(cpu["resource"]["target"]["type"], "AverageValue")

    def test_a_percentage_set_explicitly_still_wins(self) -> None:
        hpa = render_hpa("--set", "autoscaling.targetCPUUtilizationPercentage=70")
        assert hpa is not None
        cpus = [m for m in hpa["spec"]["metrics"] if m["type"] == "Resource" and m["resource"]["name"] == "cpu"]
        self.assertEqual(cpus, [{"type": "Resource", "resource": {"name": "cpu", "target": {
            "type": "Utilization", "averageUtilization": 70}}}])

    def test_event_loop_utilization_is_opt_in_and_keeps_the_cpu_fallback(self) -> None:
        hpa = render_hpa("--set", "autoscaling.eventLoopUtilization.enabled=true")
        assert hpa is not None
        elu = metric(hpa, "Pods", "pipeshub_nodejs_event_loop_utilization")
        self.assertEqual(elu, {"type": "Pods", "pods": {
            "metric": {"name": "pipeshub_nodejs_event_loop_utilization"},
            "target": {"type": "AverageValue", "averageValue": "600m"}}})
        self.assertIsNotNone(metric(hpa, "Resource", "cpu"))

    def test_event_loop_utilization_without_metrics_fails_the_render(self) -> None:
        done = subprocess.run(
            ["helm", "template", "ci", str(CHART), *SECRETS,
             "--set", "autoscaling.eventLoopUtilization.enabled=true", "--set", "metrics.enabled=false"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("needs metrics.enabled", done.stderr)

    def test_node_is_told_the_metrics_port_only_when_metrics_are_on(self) -> None:
        self.assertEqual(env(app_container(render()))["METRICS_PORT"], "9090")
        self.assertNotIn("METRICS_PORT", env(app_container(render("--set", "metrics.enabled=false"))))

    def test_compose_still_ships_one_node_process_without_a_metrics_port(self) -> None:
        compose = (CHART.parents[1] / "docker-compose" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("METRICS_PORT", compose)
        app = yaml.safe_load(compose)["services"]["pipeshub-ai"]
        self.assertNotIn("replicas", app.get("deploy", {}))


if __name__ == "__main__":
    unittest.main()

"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

Every AGENT_HARNESS_* variable the chart sets must be read by the backend.
Tuning knobs are constants and deployment behaviour comes from capability
probes, so a variable no code reads is dead configuration that misleads
operators into thinking it takes effect.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEPLOYMENT = REPO / "deployment/helm/pipeshub-ai/templates/deployment.yaml"
SOURCES = (REPO / "backend/nodejs/apps/src", REPO / "backend/python/app")
ENV_NAME = re.compile(r"-\s*name:\s*(AGENT_HARNESS_[A-Z0-9_]+)")


def chart_harness_env() -> set[str]:
    return set(ENV_NAME.findall(DEPLOYMENT.read_text(encoding="utf-8")))


def backend_text() -> str:
    parts = []
    for root in SOURCES:
        for path in root.rglob("*"):
            if path.suffix in {".ts", ".py", ".sh"} and path.is_file():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


class HarnessEnv(unittest.TestCase):
    def test_every_harness_variable_the_chart_sets_is_read_by_the_backend(self) -> None:
        names = chart_harness_env()
        self.assertTrue(names, "the chart sets no AGENT_HARNESS_* variable; this test lost its target")
        text = backend_text()
        unread = sorted(name for name in names if name not in text)
        self.assertEqual(unread, [], f"the chart sets variables no backend code reads: {unread}")


if __name__ == "__main__":
    unittest.main()

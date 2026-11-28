"""The heavy workflows run on a pull request only when it carries a `ci:` label (CONTRIBUTING.md, "Heavy CI").

Each job of a heavy workflow carries the same gate: anything but a pull request runs; on a pull
request, the label just added must be one of the workflow's labels (so an unrelated label does not
re-run it), or, on a push, the pull request must already carry one. A skipped job still reports,
so a required check never waits. The workflow keeps a schedule and *Run workflow*, so main is
still covered.

Run: python3 -m unittest discover -s scripts -p 'test_ci_label_gate.py'  (needs PyYAML)
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# Heavy workflow -> its area label (ci:full runs them all). A workflow missing from this checkout is skipped.
HEAVY = {
    "agent-harness-infrastructure.yml": "ci:harness",
    "harness-e2e.yml": "ci:harness",
    "harness-fault.yml": "ci:harness",
    "pg-runtime.yml": "ci:db",
    "pg-stores.yml": "ci:db",
    "pgvector-provider.yml": "ci:db",
    "db-migrations.yml": "ci:db",
    "docstore-migrate.yml": "ci:db",
    "permission-conformance.yml": "ci:db",
    "node-pg-runtime.yml": "ci:db",
    "postgres-image.yml": "ci:db",
    "backend-matrix.yml": "ci:db",
    "storage-backends.yml": "ci:db",
    "e2e-smoke.yml": "ci:e2e",
    "published-hub-smoke.yml": "ci:e2e",
    "arm64-smoke.yml": "ci:e2e",
    "electron-pr-build.yml": "ci:e2e",
    "native-bundle.yml": "ci:e2e",
    "sdk-tests.yml": "ci:e2e",
    "integration-tests.yml": "ci:e2e",
    "helm-chart.yml": "ci:e2e",
    "codeql.yml": "ci:security",
    "graph-explorer-tests.yml": "ci:full",
}
_LABELS = re.compile(r"contains\(fromJSON\('(\[[^\]]*\])'\), github\.event\.label\.name\)")


def gate_terms(labels: list[str]) -> list[str]:
    """The two pull request terms of the gate, as written in every heavy job."""
    has = " || ".join(f"contains(github.event.pull_request.labels.*.name, '{label}')" for label in labels)
    return [
        f"contains(fromJSON('{json.dumps(labels, separators=(',', ':'))}'), github.event.label.name)",
        f"(github.event.action != 'labeled' && ({has}))",
    ]


def gate_problems(name: str, text: str) -> list[str]:
    workflow = yaml.safe_load(text)
    on = workflow.get(True, workflow.get("on")) or {}
    problems = []
    pr = on.get("pull_request") or {}
    if "labeled" not in (pr.get("types") or []):
        problems.append(f"{name}: pull_request types must include labeled, so adding a label starts the run")
    for trigger in ("schedule", "workflow_dispatch"):
        if trigger not in on:
            problems.append(f"{name}: no {trigger} trigger, so main is not covered without a label")
    gated = 0
    for job_id, job in (workflow.get("jobs") or {}).items():
        cond = " ".join(str(job.get("if", "")).split())
        m = _LABELS.search(cond)
        if not m:
            continue
        gated += 1
        labels = json.loads(m.group(1))
        if labels[0] != "ci:full":
            problems.append(f"{name}:{job_id}: ci:full must come first in the gate's labels")
        for term in gate_terms(labels):
            if term not in cond:
                problems.append(f"{name}:{job_id}: gate term missing or edited: {term}")
        if "github.event_name != 'pull_request' ||" not in cond:
            problems.append(f"{name}:{job_id}: the gate must let every other event through")
    if not gated:
        problems.append(f"{name}: no job carries the ci: label gate")
    return problems


class CiLabelGateTest(unittest.TestCase):
    def test_heavy_workflows_are_gated(self) -> None:
        problems = []
        for name, area in HEAVY.items():
            path = WORKFLOWS / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            problems += gate_problems(name, text)
            if f'"{area}"' not in text:
                problems.append(f"{name}: its area label {area} is not in the gate")
        self.assertEqual(problems, [])

    def test_every_gated_workflow_uses_the_same_gate(self) -> None:
        problems = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if "github.event.label.name" in text and '"ci:full"' in text:
                problems += gate_problems(path.name, text)
        self.assertEqual(problems, [])

    def test_an_ungated_job_is_caught(self) -> None:
        text = "on:\n  pull_request:\n    types: [labeled]\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
        ungated = text + "jobs:\n  a:\n    runs-on: ubuntu-latest\n"
        self.assertIn("x.yml: no job carries the ci: label gate", gate_problems("x.yml", ungated))
        terms = gate_terms(["ci:full", "ci:db"])
        good = text + f"jobs:\n  a:\n    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
        self.assertEqual(gate_problems("x.yml", good), [])
        # Re-running on any label event (labels already on the pull request) is the bug the gate prevents.
        loose = good.replace("github.event.action != 'labeled' && ", "")
        self.assertTrue(any("gate term missing" in p for p in gate_problems("x.yml", loose)))


if __name__ == "__main__":
    unittest.main()

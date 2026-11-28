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
_ALLOWED = re.compile(r"github\.event\.action != 'unlabeled' && contains\(fromJSON\('(\[[^\]]*\])'\), github\.event\.label\.name\)")
_ACTED_ON = re.compile(r"github\.event\.action != 'unlabeled' \|\| contains\(fromJSON\('(\[[^\]]*\])'\), github\.event\.label\.name\)")
# A label the workflow's own conditions read, outside the gate (for example integration-tests' it-arango).
_READS = re.compile(r"(?:labels\.\*\.name, |label\.name == )'([^']+)'")


def _arr(labels: list[str]) -> str:
    return json.dumps(labels, separators=(",", ":"))


def _has_any(labels: list[str]) -> str:
    return " || ".join(f"contains(github.event.pull_request.labels.*.name, '{label}')" for label in labels)


def gate_terms(allowed: list[str], acted_on: list[str]) -> list[str]:
    """The pull request terms of the gate, as written in every heavy job.

    `allowed` start the job (added, or already on the pull request); `acted_on` adds the labels whose
    removal re-runs it (allowed plus, say, skip-it). Only the label just added or removed counts on a
    label event, so an unrelated label re-runs nothing.
    """
    return [
        f"(github.event.action != 'unlabeled' && contains(fromJSON('{_arr(allowed)}'), github.event.label.name))",
        f"(github.event.action != 'labeled' && ({_has_any(allowed)}) &&"
        f" (github.event.action != 'unlabeled' || contains(fromJSON('{_arr(acted_on)}'), github.event.label.name)))",
    ]


def group_flag(allowed: list[str], acted_on: list[str]) -> str:
    """The concurrency group's suffix: 'heavy' for an event the gate acts on, 'other' for the rest.

    Runs that act cancel each other (a push cancels a run a label started); an unrelated label lands
    in 'other' and never cancels a running suite.
    """
    return (
        f"-${{{{ (github.event_name != 'pull_request' || contains(fromJSON('{_arr(acted_on)}'), github.event.label.name) || "
        f"(github.event.action != 'labeled' && github.event.action != 'unlabeled' && ({_has_any(allowed)}))) && 'heavy' || 'other' }}}}"
    )


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
    allowed: list[str] = []
    acted_on: list[str] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        cond = " ".join(str(job.get("if", "")).split())
        m, n = _ALLOWED.search(cond), _ACTED_ON.search(cond)
        if not m:
            continue
        allowed, acted_on = json.loads(m.group(1)), json.loads(n.group(1)) if n else []
        if allowed[:1] != ["ci:full"]:
            problems.append(f"{name}:{job_id}: ci:full must come first in the gate's labels")
        if acted_on[: len(allowed)] != allowed:
            problems.append(f"{name}:{job_id}: the labels whose removal re-runs the job must start with the allowed ones")
        for term in gate_terms(allowed, acted_on):
            if term not in cond:
                problems.append(f"{name}:{job_id}: gate term missing or edited: {term}")
        if "github.event_name != 'pull_request' ||" not in cond:
            problems.append(f"{name}:{job_id}: the gate must let every other event through")
    if not allowed:
        problems.append(f"{name}: no job carries the ci: label gate")
        return problems
    unread = sorted({label for label in _READS.findall(text) if not label.startswith("ci:")} - set(acted_on))
    # A generated copy may add a start label of its own for its other jobs; its generator's tests cover that.
    if unread and not text.startswith("# Generated from"):
        problems.append(f"{name}: labels its jobs read are not in the gate, so adding or removing them skips the run: {unread}")
    group = ((workflow.get("concurrency") or {}).get("group") or "") if isinstance(workflow.get("concurrency"), dict) else ""
    if group and not group.endswith(group_flag(allowed, acted_on)):
        problems.append(f"{name}: the concurrency group must end with the gate's heavy/other flag")
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
        allowed, acted_on = ["ci:full", "ci:db"], ["ci:full", "ci:db", "skip-x"]
        terms = gate_terms(allowed, acted_on)
        group = "concurrency:\n  group: x-${{ github.ref }}" + group_flag(allowed, acted_on) + "\n"
        good = text + group + f"jobs:\n  a:\n    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
        self.assertEqual(gate_problems("x.yml", good), [])
        # Re-running on any label event (labels already on the pull request) is the bug the gate prevents.
        loose = good.replace("(github.event.action != 'labeled' && (", "((")
        self.assertTrue(any("gate term missing" in p for p in gate_problems("x.yml", loose)))

    def test_a_label_the_jobs_read_must_be_in_the_gate(self) -> None:
        allowed = ["ci:full", "ci:db"]
        terms = gate_terms(allowed, allowed)
        text = (
            "on:\n  pull_request:\n    types: [labeled]\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
            f"jobs:\n  a:\n    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
            "    env:\n      WIDE: ${{ contains(github.event.pull_request.labels.*.name, 'db-wide') }}\n"
        )
        self.assertTrue(any("db-wide" in p for p in gate_problems("x.yml", text)))

    def test_the_label_name_alone_must_not_split_the_group(self) -> None:
        allowed = ["ci:full", "ci:db"]
        terms = gate_terms(allowed, allowed)
        text = (
            "on:\n  pull_request:\n    types: [labeled]\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
            "concurrency:\n  group: x-${{ github.ref }}-${{ github.event.label.name }}\n"
            f"jobs:\n  a:\n    if: github.event_name != 'pull_request' || {terms[0]} || {terms[1]}\n"
        )
        self.assertTrue(any("concurrency group" in p for p in gate_problems("x.yml", text)))


if __name__ == "__main__":
    unittest.main()
